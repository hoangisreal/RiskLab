"""Append audit in the caller's transaction, without committing independently."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from risklab.business.models import AuditEvent


def append_audit(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    actor_id: UUID | None = None,
    actor_type: str = "USER",
    request_id: UUID | None = None,
    job_id: UUID | None = None,
    reason: str | None = None,
    changes: dict[str, str | int | bool | None] | None = None,
) -> AuditEvent:
    """Use typed, allowlisted deltas; never pass credentials or raw applicant snapshots."""
    allowed = {
        "role",
        "is_active",
        "revision",
        "state",
        "assignee_id",
        "prediction_id",
        "review_id",
        "outcome",
        "proposal",
    }
    if changes and set(changes) - allowed:
        raise ValueError("Audit changes contain a non-allowlisted field")
    event = AuditEvent(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_type=actor_type,
        request_id=request_id,
        job_id=job_id,
        reason=reason,
        details=dict(changes or {}),
    )
    session.add(event)
    return event
