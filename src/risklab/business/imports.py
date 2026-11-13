"""Transactional import primitives. Queue claims/leases and HTTP wiring belong to B03-T02/T04.

Callers own the transaction. Files/catalog/raw validation and object writes finish
before persistence; no parsing, inference or filesystem IO under database locks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, load_only

from risklab.business.audit import append_audit
from risklab.business.catalog import DemoCatalog, PinnedRelease, PreparedImport, canonical_json
from risklab.business.errors import AppError
from risklab.business.identity import ensure_application_access, identity_lock
from risklab.business.import_csv import IMPORT_CONTRACT, ImportIssue, import_fingerprint
from risklab.business.jobs import enqueue_job
from risklab.business.models import Application, ImportBatch, ImportIdempotency, User
from risklab.business.storage import LocalStorage


def manager(session: Session, actor_id: UUID) -> User:
    # Shared lock stabilizes role/status against Admin updates without lock upgrades
    # when an HTTP consumer has already authenticated in this transaction.
    actor = session.scalar(
        select(User)
        .where(User.id == actor_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if actor is None or not actor.is_active:
        raise AppError(401, "AUTHENTICATION_REQUIRED", "Sign in to continue")
    if actor.role != "MANAGER":
        raise AppError(403, "FORBIDDEN", "Only a Manager may import applications")
    return actor


def import_result(batch: ImportBatch) -> dict[str, object]:
    return {
        "id": str(batch.id),
        "revision": batch.revision,
        "state": batch.state,
        "catalog_version": batch.catalog_version,
        "import_contract_version": batch.import_contract_version,
        "created_at": batch.created_at.isoformat().replace("+00:00", "Z"),
        "attempt": batch.attempt,
        "total_rows": batch.total_rows,
        "created_count": batch.created_count,
        "ready_count": batch.ready_count,
        "incomplete_count": batch.incomplete_count,
        "retryable": batch.retryable,
        "errors": copy.deepcopy(batch.errors),
        "errors_truncated": batch.errors_truncated,
    }


@dataclass(frozen=True)
class StagedImport:
    catalog_version: str
    catalog_sha256: str
    file_sha256: str
    release_json: bytes
    payload_sha256: str


def stage_upload(
    data: bytes,
    *,
    catalog: DemoCatalog,
    storage: LocalStorage,
    release: PinnedRelease,
) -> StagedImport:
    """Trusted adapter envelope, not a user-selectable file path or model override."""
    if len(data) > 1_048_576:
        raise AppError(413, "PAYLOAD_TOO_LARGE", "Import file exceeds the limit")
    catalog.validate_release(release)
    return StagedImport(
        catalog.version,
        catalog.store(storage),
        storage.put(data),
        canonical_json(release.model_dump()),
        import_fingerprint(data, catalog_version=catalog.version),
    )


def enqueue_import(
    session: Session, *, actor_id: UUID, key: str, staged: StagedImport, request_id: UUID
) -> dict[str, object]:
    """Atomic intent + original 202 response + audit. Replay precedes active cap."""
    if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key) is None:
        raise AppError(422, "VALIDATION_ERROR", "Invalid idempotency key")
    # Serialize only this actor's import intents (including two different keys at
    # the cap). Hash collisions merely serialize unrelated actors; no permission
    # or identity is inferred from the hash. No process-local locks or TTL keys.
    lock_key = int.from_bytes(
        hashlib.blake2b(b"imports:" + actor_id.bytes, digest_size=8).digest(), signed=True
    )
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    actor = manager(session, actor_id)
    prior = session.get(ImportIdempotency, (actor.id, "imports.create", key))
    if prior is not None:
        if prior.payload_sha256 != staged.payload_sha256:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "Key was used with different input")
        return copy.deepcopy(prior.response)
    active = (
        session.scalar(
            select(func.count())
            .select_from(ImportBatch)
            .where(ImportBatch.actor_id == actor.id, ImportBatch.state.in_(["QUEUED", "RUNNING"]))
        )
        or 0
    )
    if active >= 2:
        raise AppError(429, "RATE_LIMITED", "Too many active imports")
    batch = ImportBatch(
        actor_id=actor.id,
        catalog_version=staged.catalog_version,
        catalog_sha256=staged.catalog_sha256,
        file_sha256=staged.file_sha256,
        import_contract_version=IMPORT_CONTRACT,
        release_identity=json.loads(staged.release_json),
    )
    session.add(batch)
    session.flush()
    job = enqueue_job(session, kind="IMPORT", import_id=batch.id, initiated_by=actor.id)
    result = import_result(batch)
    session.add(
        ImportIdempotency(
            actor_id=actor.id,
            operation="imports.create",
            key=key,
            payload_sha256=staged.payload_sha256,
            import_id=batch.id,
            status=202,
            response=copy.deepcopy(result),
        )
    )
    append_audit(
        session,
        action="import.created",
        job_id=job.id,
        resource_type="import",
        resource_id=batch.id,
        actor_id=actor.id,
        request_id=request_id,
        changes={"state": "QUEUED"},
    )
    session.flush()
    return result


def persist_import(
    session: Session, *, import_id: UUID, prepared: PreparedImport, job_id: UUID | None = None
) -> dict[str, object]:
    """Atomic validated applications + scoring jobs + audit, idempotent on completion.

    READY rows enqueue a pinned SCORING job in this same transaction so a crash
    can never persist an application whose scoring intent was lost. The worker
    calls this only while owning the import job attempt (attempt fencing).
    """
    # Worker needs only public account identity, never password/session access.
    # Admin mutations take the exclusive counterpart before changing accounts.
    identity_lock(session, shared=True)
    batch = session.scalar(
        select(ImportBatch)
        .where(ImportBatch.id == import_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if batch is None:
        raise AppError(404, "NOT_FOUND", "Import not found")
    if (
        batch.file_sha256 != prepared.file_sha256
        or batch.catalog_sha256 != prepared.catalog_sha256
        or batch.catalog_version != prepared.catalog_version
        or canonical_json(batch.release_identity) != prepared.release_json
    ):
        raise AppError(409, "IMPORT_IDENTITY_CONFLICT", "Prepared import identity differs")
    if batch.state in {"SUCCEEDED", "FAILED"}:
        return import_result(batch)
    # Lock job before actor. Enqueue does not lock existing jobs; completion never
    # takes identity/admin locks or updates the source application during replacement.
    actor = session.scalar(
        select(User)
        .options(load_only(User.id, User.role, User.is_active))
        .where(User.id == batch.actor_id)
        .execution_options(populate_existing=True)
    )
    if actor is None or not actor.is_active or actor.role != "MANAGER":
        raise AppError(403, "FORBIDDEN", "Import initiator is no longer an active Manager")
    issues = list(prepared.issues)
    for number, row in enumerate(prepared.rows, start=2):
        replacement = row.source.supersedes_application_id
        if replacement is not None:
            target = session.get(Application, replacement)
            if target is None:
                issues.append(
                    ImportIssue(number, "supersedes_application_id", "INVALID_REPLACEMENT")
                )
            else:
                ensure_application_access(actor, target.assignee_id)
    if issues:
        batch.state = "FAILED"
        batch.errors = [asdict(issue) for issue in issues[:100]]
        batch.errors_truncated = prepared.errors_truncated or len(issues) > 100
        batch.retryable = False
    else:
        if not 1 <= len(prepared.rows) <= 100:
            raise ValueError("Prepared import has invalid row count")
        for row in prepared.rows:
            ready = not row.missing_sources
            application = Application(
                import_id=batch.id,
                external_reference=row.source.external_reference,
                catalog_version=batch.catalog_version,
                snapshot_id=row.source.snapshot_id,
                input_sha256=row.input_sha256,
                feature_contract_version=batch.release_identity["feature_contract_version"],
                source_availability=json.loads(row.availability_json),
                missing_sources=list(row.missing_sources),
                feature_availability="READY" if ready else "INCOMPLETE",
                scoring_state="QUEUED" if ready else "NOT_REQUESTED",
                supersedes_application_id=row.source.supersedes_application_id,
            )
            session.add(application)
            session.flush()
            if ready:
                enqueue_job(
                    session,
                    kind="SCORING",
                    application_id=application.id,
                    initiated_by=batch.actor_id,
                    input_sha256=row.input_sha256,
                    release_identity=dict(batch.release_identity),
                )
            append_audit(
                session,
                action="application.imported",
                resource_type="application",
                resource_id=application.id,
                actor_type="WORKER",
                job_id=job_id,
                changes={"state": application.feature_availability},
            )
        batch.total_rows = len(prepared.rows)
        batch.created_count = len(prepared.rows)
        batch.incomplete_count = sum(bool(row.missing_sources) for row in prepared.rows)
        batch.ready_count = batch.created_count - batch.incomplete_count
        batch.state = "SUCCEEDED"
    batch.revision += 1
    append_audit(
        session,
        action="import.completed",
        resource_type="import",
        resource_id=batch.id,
        actor_type="WORKER",
        job_id=job_id,
        changes={"state": batch.state},
    )
    session.flush()
    return import_result(batch)
