"""Worker entrypoint: import and scoring handlers over the PostgreSQL queue.

Handlers read immutable objects and run inference outside transactions, then
commit results in one fenced transaction. The worker has a technical identity
only: it never creates reviews or human decisions.
"""

from __future__ import annotations

import json
import signal
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from risklab.api.service import ServiceError
from risklab.business import imports as import_service
from risklab.business.audit import append_audit
from risklab.business.catalog import DemoCatalog, PinnedRelease
from risklab.business.db import make_engine, session_factory
from risklab.business.errors import AppError
from risklab.business.jobs import claim_job, complete_job, fail_job
from risklab.business.models import Application, ImportBatch, Job, Prediction
from risklab.business.scoring import PinnedScoringModel
from risklab.business.settings import Settings, load_settings
from risklab.business.storage import LocalStorage, StorageError


@dataclass
class WorkerRuntime:
    storage: LocalStorage
    config_dir: Path
    root: Path
    scorer: PinnedScoringModel | None = None


def _fail(
    factory: object,
    job: Job,
    worker_id: str,
    *,
    code: str,
    retryable: bool,
) -> str | None:
    with factory.begin() as session:  # type: ignore[attr-defined]
        state = fail_job(
            session,
            job_id=job.id,
            attempt=job.attempt,
            worker_id=worker_id,
            error_code=code,
            retryable=retryable,
        )
        if state is not None and job.kind == "SCORING" and job.application_id is not None:
            application = session.get(Application, job.application_id)
            if application is not None:
                application.scoring_state = "FAILED" if state == "FAILED" else "QUEUED"
        if state is not None and job.kind == "IMPORT" and job.import_id is not None:
            batch = session.get(ImportBatch, job.import_id)
            if batch is not None and batch.state == "RUNNING":
                if state == "FAILED":
                    batch.state = "FAILED"
                    batch.retryable = retryable
                    batch.errors = [{"row": None, "field": None, "code": code}]
                    batch.revision += 1
                else:
                    batch.state = "QUEUED"
        return state


def _run_import(factory: object, runtime: WorkerRuntime, job: Job, worker_id: str) -> None:
    assert job.import_id is not None
    with factory.begin() as session:  # type: ignore[attr-defined]
        batch = session.get(ImportBatch, job.import_id)
        if batch is None:
            return
        file_hash, catalog_hash = batch.file_sha256, batch.catalog_sha256
        release = PinnedRelease.model_validate(batch.release_identity)
    try:
        catalog_data = runtime.storage.read(catalog_hash, max_bytes=20_000_000)
        file_data = runtime.storage.read(file_hash)
        catalog = DemoCatalog(
            catalog_data, expected_sha256=catalog_hash, config_dir=runtime.config_dir
        )
        prepared = catalog.prepare(file_data, release, runtime.storage)
    except (AppError, StorageError) as error:
        code = error.code if isinstance(error, AppError) else "DEPENDENCY_UNAVAILABLE"
        _fail(factory, job, worker_id, code=code, retryable=True)
        return
    with factory.begin() as session:  # type: ignore[attr-defined]
        current = session.get(Job, job.id)
        if (
            current is None
            or current.state != "RUNNING"
            or current.attempt != job.attempt
            or current.lease_owner != worker_id
        ):
            return  # Lease was reclaimed; the newer attempt owns completion.
        import_service.persist_import(session, import_id=job.import_id, prepared=prepared)
        complete_job(session, job_id=job.id, attempt=job.attempt, worker_id=worker_id)


def _run_scoring(factory: object, runtime: WorkerRuntime, job: Job, worker_id: str) -> None:
    assert job.application_id is not None
    scorer = runtime.scorer
    if scorer is None:
        _fail(factory, job, worker_id, code="MODEL_UNAVAILABLE", retryable=True)
        return
    try:
        pinned = PinnedRelease.model_validate(job.release_identity or {})
    except ValueError:
        _fail(factory, job, worker_id, code="MODEL_UNAVAILABLE", retryable=False)
        return
    if pinned.model_dump() != scorer.release.model_dump():
        # Retry must keep the pinned operation/model, never drift to a new release.
        _fail(factory, job, worker_id, code="MODEL_UNAVAILABLE", retryable=True)
        return
    try:
        payload = json.loads(runtime.storage.read(job.input_sha256 or ""))
        result = scorer.score(payload)
    except StorageError:
        _fail(factory, job, worker_id, code="DEPENDENCY_UNAVAILABLE", retryable=True)
        return
    except ServiceError as error:
        retryable = error.status >= 500
        _fail(
            factory,
            job,
            worker_id,
            code="MODEL_UNAVAILABLE" if retryable else "INVALID_SNAPSHOT",
            retryable=retryable,
        )
        return
    except (ValueError, TypeError):
        _fail(factory, job, worker_id, code="INVALID_SNAPSHOT", retryable=False)
        return
    release = scorer.release
    with factory.begin() as session:  # type: ignore[attr-defined]
        current = session.get(Job, job.id)
        if (
            current is None
            or current.state != "RUNNING"
            or current.attempt != job.attempt
            or current.lease_owner != worker_id
        ):
            return
        application = session.get(Application, job.application_id)
        if application is None:
            return
        existing = session.scalar(
            select(Prediction).where(Prediction.application_id == application.id)
        )
        if existing is None:
            prediction = Prediction(
                application_id=application.id,
                job_id=job.id,
                input_revision=application.input_revision,
                input_sha256=application.input_sha256,
                feature_schema_hash=result.feature_schema_hash,
                model_version=release.model_version,
                model_sha256=release.model_sha256,
                model_run_id=release.model_run_id,
                lock_id=release.lock_id,
                feature_contract_version=release.feature_contract_version,
                protocol_hashes=dict(release.protocol_hashes),
                probability=result.probability,
                threshold=result.threshold,
                policy_version=release.policy_version or "",
                sources_used=result.sources_used,
                warnings=result.warnings,
                explanation=result.explanation,
                initiated_by=job.initiated_by,
            )
            session.add(prediction)
            session.flush()
        else:
            prediction = existing
        application.scoring_state = "SUCCEEDED"
        append_audit(
            session,
            action="application.scored",
            resource_type="application",
            resource_id=application.id,
            actor_type="WORKER",
            job_id=job.id,
            changes={"state": "SUCCEEDED", "prediction_id": str(prediction.id)},
        )
        complete_job(session, job_id=job.id, attempt=job.attempt, worker_id=worker_id)


def run_once(engine: object, runtime: WorkerRuntime, *, worker_id: str) -> bool:
    """Claim and process one job. Returns False when no job was due."""
    factory = session_factory(engine)  # type: ignore[arg-type]
    with factory.begin() as session:
        job = claim_job(session, worker_id=worker_id)
        if job is None:
            return False
        claimed = Job(
            kind=job.kind,
            import_id=job.import_id,
            application_id=job.application_id,
            initiated_by=job.initiated_by,
            input_sha256=job.input_sha256,
            release_identity=job.release_identity,
        )
        claimed.id, claimed.attempt = job.id, job.attempt
        if job.kind == "IMPORT" and job.import_id is not None:
            batch = session.get(ImportBatch, job.import_id)
            if batch is not None and batch.state == "QUEUED":
                batch.state = "RUNNING"
        if job.kind == "SCORING" and job.application_id is not None:
            application = session.get(Application, job.application_id)
            if application is not None:
                application.scoring_state = "RUNNING"
    handler = _run_import if claimed.kind == "IMPORT" else _run_scoring
    try:
        handler(factory, runtime, claimed, worker_id)
    except Exception:
        _fail(factory, claimed, worker_id, code="WORKER_ERROR", retryable=True)
    return True


def build_runtime(settings: Settings) -> WorkerRuntime:
    storage = LocalStorage(settings.storage_root)
    scorer: PinnedScoringModel | None = None
    if settings.model_file is not None and settings.release_file is not None:
        try:
            release = PinnedRelease.model_validate_json(settings.release_file.read_bytes())
            scorer = PinnedScoringModel(
                model_path=settings.model_file,
                release=release,
                config_dir=settings.config_dir,
                root=settings.root,
            )
        except Exception:
            scorer = None  # Readiness stays false; scoring jobs fail retryably.
    return WorkerRuntime(
        storage=storage, config_dir=settings.config_dir, root=settings.root, scorer=scorer
    )


def main() -> None:
    settings = load_settings()
    engine = make_engine(settings)
    if engine is None:
        raise SystemExit("RISKLAB_DATABASE_URL is required for the worker")
    runtime = build_runtime(settings)
    worker_id = f"{socket.gethostname()}-{id(object())}"
    stopped = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped:
            if not run_once(engine, runtime, worker_id=worker_id):
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
