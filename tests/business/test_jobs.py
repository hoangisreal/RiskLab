"""Real PostgreSQL lease expiry, stale identity maps and completion races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from risklab.business.db import session_factory
from risklab.business.jobs import claim_job, complete_job, enqueue_job, fail_job, heartbeat
from risklab.business.models import ImportBatch, Job, User, now_utc

pytestmark = pytest.mark.integration


@pytest.fixture
def queued(database):
    factory = session_factory(database.runtime)
    with factory.begin() as session:
        user = User(username="jobs.manager", role="MANAGER", password_hash="synthetic")
        session.add(user)
        session.flush()
        batch = ImportBatch(
            actor_id=user.id,
            catalog_version="test",
            catalog_sha256="a" * 64,
            file_sha256="b" * 64,
            import_contract_version="demo-import/1",
            release_identity={},
            state="RUNNING",
        )
        session.add(batch)
        session.flush()
        job = enqueue_job(session, kind="IMPORT", import_id=batch.id, initiated_by=user.id)
        job_id = job.id
    return factory, job_id


@pytest.mark.parametrize("operation", ["complete", "fail", "heartbeat"])
def test_cached_old_attempt_cannot_overwrite_reclaimed_job(queued, operation):
    factory, job_id = queued
    with factory.begin() as session:
        claimed = claim_job(session, worker_id="old")
        claimed.lease_expires_at = now_utc() - timedelta(seconds=1)
    with factory.begin() as stale:
        cached = stale.get(Job, job_id)
        with factory.begin() as other:
            assert claim_job(other, worker_id="new").attempt == 2
        kwargs = dict(job_id=job_id, attempt=1, worker_id="old")
        if operation == "complete":
            assert complete_job(stale, **kwargs) is False
        elif operation == "fail":
            assert fail_job(stale, **kwargs, error_code="OLD_ERROR", retryable=False) is None
        else:
            assert heartbeat(stale, **kwargs) is False
        assert cached is not None
    with factory() as session:
        job = session.get(Job, job_id)
        assert (job.state, job.attempt, job.lease_owner) == ("RUNNING", 2, "new")


def test_completion_holds_job_lock_until_transaction_commits(queued):
    factory, job_id = queued
    with factory.begin() as session:
        assert claim_job(session, worker_id="owner")
    with factory.begin() as completion:
        assert complete_job(completion, job_id=job_id, attempt=1, worker_id="owner")
        # A competing claim cannot see the uncommitted success or reclaim its row.
        with ThreadPoolExecutor(max_workers=1) as pool:

            def claim():
                with factory.begin() as session:
                    return claim_job(session, worker_id="competitor")

            assert pool.submit(claim).result(timeout=5) is None
    with factory() as session:
        assert session.get(Job, job_id).state == "SUCCEEDED"


def test_repeated_crashes_stop_at_attempt_budget_and_update_import(queued):
    factory, job_id = queued
    for attempt in range(1, 4):
        with factory.begin() as session:
            claimed = claim_job(session, worker_id=f"worker-{attempt}")
            assert claimed.attempt == attempt
            claimed.lease_expires_at = now_utc() - timedelta(seconds=1)
    with factory.begin() as session:
        assert claim_job(session, worker_id="fourth") is None
    with factory() as session:
        job = session.get(Job, job_id)
        batch = session.get(ImportBatch, job.import_id)
        assert (job.state, job.attempt, job.lease_owner) == ("FAILED", 3, None)
        assert job.last_error_code == "LEASE_EXPIRED"
        assert batch.state == "FAILED" and batch.retryable


def test_expired_owner_cannot_renew_or_commit(queued):
    factory, job_id = queued
    with factory.begin() as session:
        job = claim_job(session, worker_id="old")
        job.lease_expires_at = now_utc() - timedelta(seconds=1)
    with factory.begin() as session:
        assert heartbeat(session, job_id=job_id, attempt=1, worker_id="old") is False
        assert complete_job(session, job_id=job_id, attempt=1, worker_id="old") is False
    with factory() as session:
        assert session.scalar(select(Job.state).where(Job.id == job_id)) == "RUNNING"


def test_worker_renews_lease_during_long_handler(queued, monkeypatch, tmp_path):
    from threading import Event

    import risklab.business.worker as worker

    factory, job_id = queued
    renewed = Event()
    original = worker.heartbeat

    def heartbeat_observed(*args, **kwargs):
        result = original(*args, **kwargs)
        renewed.set()
        return result

    monkeypatch.setattr(worker, "LEASE_SECONDS", 0.15)
    monkeypatch.setattr(worker, "heartbeat", heartbeat_observed)

    def slow_handler(factory, runtime, job, worker_id):
        assert renewed.wait(5)
        with factory.begin() as session:
            assert complete_job(session, job_id=job.id, attempt=job.attempt, worker_id=worker_id)

    monkeypatch.setattr(worker, "_run_import", slow_handler)
    from types import SimpleNamespace

    assert worker.run_once(
        factory.kw["bind"], SimpleNamespace(scorer=None), worker_id="long-handler"
    )
    with factory() as session:
        assert session.get(Job, job_id).state == "SUCCEEDED"
