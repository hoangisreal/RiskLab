"""Identity and audit persistence; domain tables are added with their own features."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from risklab.business.db import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('ANALYST','MANAGER','ADMIN')", name="users_role_check"),
        CheckConstraint("revision >= 1", name="users_revision_check"),
        CheckConstraint("username ~ '^[a-z0-9_.-]{3,64}$'", name="users_username_check"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LoginSession(Base):
    __tablename__ = "sessions"
    # Only digests are persisted; a database read does not reveal a bearer cookie.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('USER','WORKER','SYSTEM')", name="audit_events_actor_type_check"
        ),
        Index("audit_resource_time", "resource_type", "resource_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_type: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID | None] = mapped_column()
    job_id: Mapped[UUID | None] = mapped_column()
    reason: Mapped[str | None] = mapped_column(String(2000))
    # "metadata" is reserved by SQLAlchemy's declarative API.
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LoginLimit(Base):
    __tablename__ = "login_limits"
    __table_args__ = (CheckConstraint("attempts >= 1", name="login_limits_attempts_check"),)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column()


class ImportBatch(Base):
    __tablename__ = "imports"
    __table_args__ = (
        CheckConstraint("state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="imports_state"),
        CheckConstraint("revision >= 1 AND attempt >= 1", name="imports_revision"),
        CheckConstraint(
            "file_sha256 ~ '^[0-9a-f]{64}$' AND catalog_sha256 ~ '^[0-9a-f]{64}$'",
            name="imports_hashes",
        ),
        CheckConstraint("total_rows IS NULL OR total_rows BETWEEN 1 AND 100", name="imports_rows"),
        CheckConstraint(
            "created_count >= 0 AND ready_count >= 0 AND incomplete_count >= 0 "
            "AND created_count = ready_count + incomplete_count",
            name="imports_counts",
        ),
        CheckConstraint(
            "(state = 'SUCCEEDED' AND total_rows IS NOT NULL AND created_count = total_rows) "
            "OR (state <> 'SUCCEEDED' AND created_count = 0)",
            name="imports_completion",
        ),
        Index("imports_actor_state", "actor_id", "state"),
        Index("imports_created", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    catalog_version: Mapped[str] = mapped_column(String(64))
    catalog_sha256: Mapped[str] = mapped_column(String(64))
    file_sha256: Mapped[str] = mapped_column(String(64))
    import_contract_version: Mapped[str] = mapped_column(String(32))
    release_identity: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), default="QUEUED")
    revision: Mapped[int] = mapped_column(default=1)
    attempt: Mapped[int] = mapped_column(default=1)
    total_rows: Mapped[int | None] = mapped_column()
    created_count: Mapped[int] = mapped_column(default=0)
    ready_count: Mapped[int] = mapped_column(default=0)
    incomplete_count: Mapped[int] = mapped_column(default=0)
    retryable: Mapped[bool] = mapped_column(default=False)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    errors_truncated: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("import_id", "external_reference", name="application_source_identity"),
        CheckConstraint(
            "external_reference ~ '^[A-Za-z0-9_-]{1,64}$' "
            "AND snapshot_id ~ '^[A-Za-z0-9_-]{1,64}$'",
            name="application_references",
        ),
        CheckConstraint("input_sha256 ~ '^[0-9a-f]{64}$'", name="application_input_hash"),
        CheckConstraint("input_revision = 1 AND revision >= 1", name="application_revision"),
        CheckConstraint(
            "feature_availability IN ('READY','INCOMPLETE','INVALID')",
            name="application_availability",
        ),
        CheckConstraint(
            "scoring_state IN ('NOT_REQUESTED','QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="application_scoring_state",
        ),
        CheckConstraint(
            "review_state IN ('UNASSIGNED','ASSIGNED','IN_REVIEW','PENDING_DECISION','DECIDED')",
            name="application_review_state",
        ),
        CheckConstraint(
            "supersedes_application_id IS NULL OR supersedes_application_id <> id",
            name="application_replacement",
        ),
        Index("applications_assignee", "assignee_id", "created_at", "id"),
        Index("applications_created", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    import_id: Mapped[UUID] = mapped_column(ForeignKey("imports.id"), index=True)
    external_reference: Mapped[str] = mapped_column(String(64))
    catalog_version: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    input_revision: Mapped[int] = mapped_column(default=1)
    feature_contract_version: Mapped[str] = mapped_column(String(32))
    source_availability: Mapped[dict[str, str]] = mapped_column(JSONB)
    missing_sources: Mapped[list[str]] = mapped_column(JSONB)
    feature_availability: Mapped[str] = mapped_column(String(16))
    scoring_state: Mapped[str] = mapped_column(String(16), default="NOT_REQUESTED")
    review_state: Mapped[str] = mapped_column(String(32), default="UNASSIGNED")
    assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    supersedes_application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"))
    revision: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('IMPORT','SCORING')", name="jobs_kind"),
        CheckConstraint("state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="jobs_state"),
        CheckConstraint("attempt >= 0 AND max_attempts BETWEEN 1 AND 10", name="jobs_attempts"),
        CheckConstraint(
            "(kind = 'IMPORT' AND import_id IS NOT NULL AND application_id IS NULL) "
            "OR (kind = 'SCORING' AND application_id IS NOT NULL AND import_id IS NULL)",
            name="jobs_target",
        ),
        Index("jobs_claim", "state", "available_at", "created_at", "id"),
        Index(
            "jobs_one_active_import",
            "import_id",
            unique=True,
            postgresql_where=text("kind = 'IMPORT' AND state IN ('QUEUED','RUNNING')"),
        ),
        Index(
            "jobs_one_active_scoring",
            "application_id",
            unique=True,
            postgresql_where=text("kind = 'SCORING' AND state IN ('QUEUED','RUNNING')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    import_id: Mapped[UUID | None] = mapped_column(ForeignKey("imports.id"), index=True)
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    state: Mapped[str] = mapped_column(String(16), default="QUEUED")
    attempt: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(default=False)
    initiated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    # Scoring pins its input and release at creation; a later release change
    # must never silently re-score under a different model identity.
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    release_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("application_id", name="prediction_per_application"),
        CheckConstraint("input_sha256 ~ '^[0-9a-f]{64}$'", name="prediction_input_hash"),
        CheckConstraint("model_sha256 ~ '^[0-9a-f]{64}$'", name="prediction_model_hash"),
        CheckConstraint("probability >= 0 AND probability <= 1", name="prediction_probability"),
        CheckConstraint("threshold >= 0 AND threshold <= 1", name="prediction_threshold"),
        Index("predictions_created", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"))
    input_revision: Mapped[int] = mapped_column()
    input_sha256: Mapped[str] = mapped_column(String(64))
    feature_schema_hash: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(128))
    model_sha256: Mapped[str] = mapped_column(String(64))
    model_run_id: Mapped[str] = mapped_column(String(128))
    lock_id: Mapped[str] = mapped_column(String(128))
    feature_contract_version: Mapped[str] = mapped_column(String(32))
    protocol_hashes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    probability: Mapped[float] = mapped_column()
    threshold: Mapped[float] = mapped_column()
    policy_version: Mapped[str] = mapped_column(String(128))
    sources_used: Mapped[list[str]] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    initiated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("application_id", "review_number", name="review_number_per_application"),
        CheckConstraint("review_number >= 1", name="reviews_number"),
        CheckConstraint(
            "proposal IS NULL OR proposal IN ('APPROVE','DECLINE')", name="reviews_proposal"
        ),
        Index("reviews_application", "application_id", "review_number"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"))
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    review_number: Mapped[int] = mapped_column()
    prediction_id: Mapped[UUID] = mapped_column(ForeignKey("predictions.id"))
    notes: Mapped[str] = mapped_column(Text, default="")
    proposal: Mapped[str | None] = mapped_column(String(16))
    # Submitted revisions are immutable; only draft columns may change.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        UniqueConstraint("application_id", name="decision_per_application"),
        CheckConstraint(
            "outcome IN ('APPROVED_SIMULATED','DECLINED_SIMULATED')", name="decisions_outcome"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"))
    review_id: Mapped[UUID] = mapped_column(ForeignKey("reviews.id"))
    prediction_id: Mapped[UUID] = mapped_column(ForeignKey("predictions.id"))
    decided_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    outcome: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class DecisionIdempotency(Base):
    __tablename__ = "decision_idempotency"
    __table_args__ = (
        CheckConstraint("operation = 'decisions.create'", name="decision_idempotency_operation"),
        CheckConstraint(
            "key ~ '^[A-Za-z0-9_-]{8,128}$' AND payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="decision_idempotency_key",
        ),
        CheckConstraint("status = 201", name="decision_idempotency_status"),
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    operation: Mapped[str] = mapped_column(String(32), primary_key=True)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    decision_id: Mapped[UUID] = mapped_column(ForeignKey("decisions.id"), unique=True)
    status: Mapped[int] = mapped_column()
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ImportIdempotency(Base):
    __tablename__ = "import_idempotency"
    __table_args__ = (
        CheckConstraint("operation = 'imports.create'", name="import_idempotency_operation"),
        CheckConstraint(
            "key ~ '^[A-Za-z0-9_-]{8,128}$' AND payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="import_idempotency_key",
        ),
        CheckConstraint("status = 202", name="import_idempotency_status"),
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    operation: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    import_id: Mapped[UUID] = mapped_column(ForeignKey("imports.id"), unique=True)
    status: Mapped[int] = mapped_column()
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
