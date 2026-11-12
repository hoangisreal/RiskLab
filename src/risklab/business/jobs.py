"""PostgreSQL job queue: short claim transactions, leases and attempt fencing.

Execution is at-least-once. A worker computes outside any transaction, then
commits its result only while it still owns the current attempt; a stale
worker whose lease was reclaimed can never overwrite a newer attempt.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from risklab.business.models import Job, now_utc

LEASE_SECONDS = 120
MAX_BACKOFF_SECONDS = 300


def enqueue_job(
    session: Session,
    *,
    kind: str,
    import_id: UUID | None = None,
    application_id: UUID | None = None,
    initiated_by: UUID | None = None,
    input_sha256: str | None = None,
    release_identity: dict[str, object] | None = None,
) -> Job:
    """Create a queued job inside the caller's transaction (no separate outbox)."""
    job = Job(
        kind=kind,
        import_id=import_id,
        application_id=application_id,
        initiated_by=initiated_by,
        input_sha256=input_sha256,
        release_identity=release_identity,
    )
    session.add(job)
    session.flush()
    return job


def claim_job(session: Session, *, worker_id: str) -> Job | None:
    """Claim the oldest due job, reclaiming expired leases, in one short transaction."""
    now = now_utc()
    job = session.scalar(
        select(Job)
        .where(
            or_(
                (Job.state == "QUEUED") & (Job.available_at <= now),
                (Job.state == "RUNNING") & (Job.lease_expires_at < now),
            )
        )
        .order_by(Job.created_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.state = "RUNNING"
    job.attempt += 1
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    job.last_error_code = None
    job.retryable = False
    session.flush()
    return job


def heartbeat(session: Session, *, job_id: UUID, attempt: int, worker_id: str) -> bool:
    job = session.get(Job, job_id)
    if (
        job is None
        or job.state != "RUNNING"
        or job.attempt != attempt
        or job.lease_owner != worker_id
    ):
        return False
    job.lease_expires_at = now_utc() + timedelta(seconds=LEASE_SECONDS)
    session.flush()
    return True


def _owned(session: Session, job_id: UUID, attempt: int, worker_id: str) -> Job | None:
    job = session.get(Job, job_id)
    if (
        job is None
        or job.state != "RUNNING"
        or job.attempt != attempt
        or job.lease_owner != worker_id
    ):
        return None
    return job


def complete_job(session: Session, *, job_id: UUID, attempt: int, worker_id: str) -> bool:
    """Mark success only for the current attempt owner; callers commit atomically."""
    job = _owned(session, job_id, attempt, worker_id)
    if job is None:
        return False
    job.state = "SUCCEEDED"
    job.lease_owner = None
    job.lease_expires_at = None
    session.flush()
    return True


def fail_job(
    session: Session,
    *,
    job_id: UUID,
    attempt: int,
    worker_id: str,
    error_code: str,
    retryable: bool,
) -> str | None:
    """Requeue with bounded backoff or fail terminally. Returns the new state."""
    job = _owned(session, job_id, attempt, worker_id)
    if job is None:
        return None
    job.last_error_code = error_code
    job.lease_owner = None
    job.lease_expires_at = None
    if retryable and job.attempt < job.max_attempts:
        job.state = "QUEUED"
        job.retryable = False
        backoff = min(2**job.attempt, MAX_BACKOFF_SECONDS)
        job.available_at = now_utc() + timedelta(seconds=backoff)
    else:
        job.state = "FAILED"
        # Terminal dependency failures stay manually retryable; validation
        # failures carry retryable=False from the handler.
        job.retryable = retryable
    session.flush()
    return job.state
