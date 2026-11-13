"""Import, application, review, decision and report endpoints (B03-B05).

Every mutation re-authenticates inside its transaction, checks object access
before state, and writes its audit event in the same commit. Model output is
stored evidence only; human proposals and decisions stay separate concepts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from risklab.business.accounts import no_query, transaction
from risklab.business.audit import append_audit
from risklab.business.catalog import canonical_json
from risklab.business.errors import AppError
from risklab.business.identity import authenticate, ensure_application_access
from risklab.business.import_csv import IMPORT_CONTRACT
from risklab.business.imports import enqueue_import, import_result, stage_upload
from risklab.business.jobs import enqueue_job
from risklab.business.models import (
    Application,
    AuditEvent,
    Decision,
    DecisionIdempotency,
    ImportBatch,
    Job,
    Prediction,
    Review,
    User,
    now_utc,
)
from risklab.business.schemas import (
    ApplicationQuery,
    AssignmentInput,
    DecisionInput,
    Envelope,
    ImportQuery,
    Page,
    PaginationQuery,
    RetryImportInput,
    ReturnInput,
    ReviewPatchInput,
    ReviewStartInput,
)

router = APIRouter(prefix="/api/v1")

LIMITATIONS = [
    "Decision-support simulation only; not a real credit approval system.",
    "TARGET means historical payment difficulties, not verified default probability.",
    "Explanations are log-odds model contributions, not causal or legal reasons.",
]


def _ts(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _user_ref(session: Session, user_id: UUID | None) -> dict[str, str] | None:
    if user_id is None:
        return None
    user = session.get(User, user_id)
    return {"id": str(user_id), "username": user.username} if user else None


def _prediction(session: Session, application_id: UUID) -> Prediction | None:
    return session.scalar(select(Prediction).where(Prediction.application_id == application_id))


def _current_review(session: Session, application: Application) -> Review | None:
    reviews = list(
        session.scalars(
            select(Review)
            .where(Review.application_id == application.id)
            .order_by(Review.review_number.desc())
        )
    )
    if not reviews:
        return None
    if application.review_state == "PENDING_DECISION":
        return next((row for row in reviews if row.submitted_at is not None), None)
    if application.review_state == "IN_REVIEW":
        return next((row for row in reviews if row.submitted_at is None), None)
    if application.review_state == "DECIDED":
        decision = session.scalar(select(Decision).where(Decision.application_id == application.id))
        return session.get(Review, decision.review_id) if decision else None
    return None


def _review_out(review: Review | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "id": str(review.id),
        "application_id": str(review.application_id),
        "author_id": str(review.author_id),
        "review_number": review.review_number,
        "prediction_id": str(review.prediction_id),
        "notes": review.notes,
        "proposal": review.proposal,
        "submitted_at": _ts(review.submitted_at) if review.submitted_at else None,
    }


def _summary(session: Session, application: Application) -> dict[str, Any]:
    prediction = _prediction(session, application.id)
    return {
        "id": str(application.id),
        "import_id": str(application.import_id),
        "external_reference": application.external_reference,
        "assignee": _user_ref(session, application.assignee_id),
        "revision": application.revision,
        "feature_availability": application.feature_availability,
        "scoring_state": application.scoring_state,
        "review_state": application.review_state,
        "prediction_id": str(prediction.id) if prediction else None,
        "created_at": _ts(application.created_at),
    }


def _detail(session: Session, application: Application) -> dict[str, Any]:
    result = _summary(session, application)
    decision = session.scalar(select(Decision).where(Decision.application_id == application.id))
    result.update(
        {
            "source_availability": application.source_availability,
            "missing_sources": application.missing_sources,
            "supersedes_application_id": str(application.supersedes_application_id)
            if application.supersedes_application_id
            else None,
            "catalog_version": application.catalog_version,
            "snapshot_id": application.snapshot_id,
            "input_sha256": application.input_sha256,
            "input_revision": application.input_revision,
            "current_review": _review_out(_current_review(session, application)),
            "decision": {
                "id": str(decision.id),
                "application_id": str(decision.application_id),
                "review_id": str(decision.review_id),
                "prediction_id": str(decision.prediction_id),
                "decided_by": str(decision.decided_by),
                "outcome": decision.outcome,
                "reason": decision.reason,
                "created_at": _ts(decision.created_at),
            }
            if decision
            else None,
        }
    )
    return result


def _load_application(
    session: Session, actor: User, application_id: UUID, *, lock: bool = False
) -> Application:
    if lock:
        # Serialize competing mutations on this row; the loser re-reads the
        # committed state and fails its revision/state guard instead of
        # writing a second side effect.
        application = session.scalar(
            select(Application).where(Application.id == application_id).with_for_update()
        )
    else:
        application = session.get(Application, application_id)
    if application is None:
        raise AppError(404, "NOT_FOUND", "Application not found")
    ensure_application_access(actor, application.assignee_id)
    return application


def _check_revision(application: Application, expected: int) -> None:
    if application.revision != expected:
        raise AppError(409, "REVISION_CONFLICT", "Application changed; reload before retrying")


def _audit(
    session: Session,
    request: Request,
    actor: User,
    application: Application,
    action: str,
    *,
    reason: str | None = None,
    changes: dict[str, str | int | bool | None] | None = None,
    job_id: UUID | None = None,
) -> None:
    append_audit(
        session,
        action=action,
        resource_type="application",
        resource_id=application.id,
        actor_id=actor.id,
        request_id=UUID(request.state.request_id),
        reason=reason,
        changes=changes,
        job_id=job_id,
    )


def _page(rows: list[Any], query: PaginationQuery, total: int) -> Page[Any]:
    return Page(
        data=rows, pagination={"page": query.page, "page_size": query.page_size, "total": total}
    )


# --- Demo catalog and imports -------------------------------------------------


@router.get("/demo-catalogs", dependencies=[Depends(no_query)])
def demo_catalogs(request: Request) -> Envelope[list[dict[str, Any]]]:
    with transaction(request) as session:
        authenticate(session, request, {"MANAGER"})
        catalog = request.app.state.catalog
        if catalog is None:
            return Envelope(data=[])
        return Envelope(
            data=[
                {
                    "version": catalog.version,
                    "feature_contract_version": catalog.feature_contract_version,
                    "snapshot_count": catalog.snapshot_count,
                }
            ]
        )


@router.post("/imports", status_code=202)
async def create_import(
    request: Request,
    import_contract_version: Annotated[str, Form()],
    catalog_version: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    # Reject unauthorized uploads before writing immutable objects; authenticate
    # again when committing because account permissions can change during IO.
    with transaction(request) as session:
        authenticate(session, request, {"MANAGER"}, mutation=True)
    if idempotency_key is None or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", idempotency_key) is None:
        raise AppError(422, "VALIDATION_ERROR", "A valid Idempotency-Key header is required")
    if import_contract_version != IMPORT_CONTRACT:
        raise AppError(422, "UNSUPPORTED_CONTRACT", "Unsupported import contract version")
    catalog = request.app.state.catalog
    release = request.app.state.release
    if catalog is None or release is None:
        raise AppError(503, "DEPENDENCY_UNAVAILABLE", "Demo catalog is not configured")
    if catalog_version != catalog.version:
        raise AppError(422, "VALIDATION_ERROR", "Unknown catalog version")
    data = await file.read()
    staged = stage_upload(data, catalog=catalog, storage=request.app.state.storage, release=release)
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"}, mutation=True)
        result = enqueue_import(
            session,
            actor_id=actor.id,
            key=idempotency_key,
            staged=staged,
            request_id=UUID(request.state.request_id),
        )
    return JSONResponse(status_code=202, content={"data": result})


def _owned_import(
    session: Session, actor: User, import_id: UUID, *, lock: bool = False
) -> ImportBatch:
    query = select(ImportBatch).where(ImportBatch.id == import_id)
    if lock:
        query = query.with_for_update()
    batch = session.scalar(query)
    if batch is None or batch.actor_id != actor.id:
        raise AppError(404, "NOT_FOUND", "Import not found")
    return batch


@router.get("/imports")
def list_imports(request: Request, query: Annotated[ImportQuery, Query()]) -> Page[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"})
        statement = select(ImportBatch).where(ImportBatch.actor_id == actor.id)
        if query.state is not None:
            statement = statement.where(ImportBatch.state == query.state)
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = session.scalars(
            statement.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        result = [import_result(row) for row in rows]
    return _page(result, query, total)


@router.get("/imports/{import_id}", dependencies=[Depends(no_query)])
def get_import(import_id: UUID, request: Request) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"})
        result = import_result(_owned_import(session, actor, import_id))
    return Envelope(data=result)


@router.post("/imports/{import_id}/retry", status_code=202, dependencies=[Depends(no_query)])
def retry_import(import_id: UUID, body: RetryImportInput, request: Request) -> JSONResponse:
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"}, mutation=True)
        batch = _owned_import(session, actor, import_id, lock=True)
        if batch.revision != body.expected_revision:
            raise AppError(409, "REVISION_CONFLICT", "Import changed; reload before retrying")
        if batch.state != "FAILED" or not batch.retryable:
            raise AppError(409, "INVALID_STATE", "Only a failed retryable import can be retried")
        batch.state = "QUEUED"
        batch.attempt += 1
        batch.retryable = False
        batch.errors = []
        batch.errors_truncated = False
        batch.total_rows = None
        batch.revision += 1
        job = enqueue_job(session, kind="IMPORT", import_id=batch.id, initiated_by=actor.id)
        append_audit(
            session,
            action="import.retried",
            job_id=job.id,
            resource_type="import",
            resource_id=batch.id,
            actor_id=actor.id,
            request_id=UUID(request.state.request_id),
            reason=body.reason,
            changes={"state": "QUEUED"},
        )
        result = import_result(batch)
    return JSONResponse(status_code=202, content={"data": result})


# --- Applications ---------------------------------------------------------------


@router.get("/applications")
def list_applications(
    request: Request, query: Annotated[ApplicationQuery, Query()]
) -> Page[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST", "MANAGER"})
        if actor.role == "ANALYST":
            if query.assignee_id is not None and query.assignee_id != actor.id:
                raise AppError(403, "FORBIDDEN", "Analysts can only list their own applications")
            statement = select(Application).where(Application.assignee_id == actor.id)
        else:
            statement = select(Application)
            if query.assignee_id is not None:
                statement = statement.where(Application.assignee_id == query.assignee_id)
        if query.q is not None:
            statement = statement.where(
                Application.external_reference.contains(query.q, autoescape=True)
            )
        if query.review_state is not None:
            statement = statement.where(Application.review_state == query.review_state)
        if query.scoring_state is not None:
            statement = statement.where(Application.scoring_state == query.scoring_state)
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = session.scalars(
            statement.order_by(Application.created_at.desc(), Application.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        result = [_summary(session, row) for row in rows]
    return _page(result, query, total)


@router.get("/applications/{application_id}", dependencies=[Depends(no_query)])
def get_application(application_id: UUID, request: Request) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST", "MANAGER"})
        result = _detail(session, _load_application(session, actor, application_id))
    return Envelope(data=result)


@router.get("/applications/{application_id}/snapshot", dependencies=[Depends(no_query)])
def get_snapshot(application_id: UUID, request: Request) -> JSONResponse:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST", "MANAGER"})
        application = _load_application(session, actor, application_id)
        key = application.input_sha256
    try:
        payload = json.loads(request.app.state.storage.read(key, max_bytes=5_000_000))
    except Exception:
        raise AppError(503, "DEPENDENCY_UNAVAILABLE", "Snapshot is unavailable") from None
    return JSONResponse(content={"data": payload})


@router.get("/applications/{application_id}/prediction", dependencies=[Depends(no_query)])
def get_prediction(application_id: UUID, request: Request) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST", "MANAGER"})
        application = _load_application(session, actor, application_id)
        prediction = _prediction(session, application.id)
        if prediction is None:
            raise AppError(404, "NOT_FOUND", "Prediction not found")
        result = {
            "id": str(prediction.id),
            "application_id": str(prediction.application_id),
            "input_revision": prediction.input_revision,
            "input_sha256": prediction.input_sha256,
            "feature_schema_hash": prediction.feature_schema_hash,
            "model_version": prediction.model_version,
            "model_sha256": prediction.model_sha256,
            "model_run_id": prediction.model_run_id,
            "lock_id": prediction.lock_id,
            "feature_contract_version": prediction.feature_contract_version,
            "protocol_hashes": prediction.protocol_hashes,
            "probability": prediction.probability,
            "threshold": prediction.threshold,
            "policy_version": prediction.policy_version,
            "sources_used": prediction.sources_used,
            "warnings": prediction.warnings,
            "explanation": prediction.explanation,
            "created_at": _ts(prediction.created_at),
            "initiated_by": str(prediction.initiated_by) if prediction.initiated_by else None,
            "job_id": str(prediction.job_id),
        }
    return Envelope(data=result)


@router.get("/applications/{application_id}/history")
def get_history(
    application_id: UUID,
    request: Request,
    query: Annotated[PaginationQuery, Query()],
) -> Page[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST", "MANAGER"})
        application = _load_application(session, actor, application_id)
        statement = select(AuditEvent).where(
            AuditEvent.resource_type == "application",
            AuditEvent.resource_id == application.id,
        )
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = session.scalars(
            statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        result = [
            {
                "id": str(row.id),
                "actor_id": str(row.actor_id) if row.actor_id else None,
                "actor_type": row.actor_type,
                "action": row.action,
                "resource_id": str(row.resource_id),
                "created_at": _ts(row.created_at),
                "request_id": str(row.request_id) if row.request_id else None,
                "job_id": str(row.job_id) if row.job_id else None,
                "reason": row.reason,
                "metadata": row.details,
            }
            for row in rows
        ]
    return _page(result, query, total)


@router.put("/applications/{application_id}/assignment", dependencies=[Depends(no_query)])
def assign_application(
    application_id: UUID, body: AssignmentInput, request: Request
) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"}, mutation=True)
        application = _load_application(session, actor, application_id, lock=True)
        _check_revision(application, body.expected_revision)
        if application.review_state == "DECIDED":
            raise AppError(409, "INVALID_STATE", "Decided applications cannot be reassigned")
        assignee = session.scalar(
            select(User)
            .where(User.id == body.assignee_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if assignee is None or not assignee.is_active or assignee.role != "ANALYST":
            raise AppError(422, "VALIDATION_ERROR", "Assignee must be an active Analyst")
        application.assignee_id = assignee.id
        application.review_state = "ASSIGNED"
        application.revision += 1
        _audit(
            session,
            request,
            actor,
            application,
            "application.assigned",
            reason=body.reason,
            changes={"state": "ASSIGNED", "assignee_id": str(assignee.id)},
        )
        result = _detail(session, application)
    return Envelope(data=result)


@router.post(
    "/applications/{application_id}/scoring/retry",
    status_code=202,
    dependencies=[Depends(no_query)],
)
def retry_scoring(application_id: UUID, body: RetryImportInput, request: Request) -> JSONResponse:
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"}, mutation=True)
        application = _load_application(session, actor, application_id, lock=True)
        _check_revision(application, body.expected_revision)
        if application.scoring_state != "FAILED" or _prediction(session, application.id):
            raise AppError(
                409, "INVALID_STATE", "Only failed scoring without a prediction can be retried"
            )
        last = session.scalar(
            select(Job)
            .where(Job.application_id == application.id, Job.kind == "SCORING")
            .order_by(Job.created_at.desc(), Job.id.desc())
        )
        if last is None or last.state != "FAILED" or not last.retryable:
            raise AppError(409, "INVALID_STATE", "The failed scoring job is not retryable")
        job = enqueue_job(
            session,
            kind="SCORING",
            application_id=application.id,
            initiated_by=actor.id,
            input_sha256=last.input_sha256,
            release_identity=dict(last.release_identity or {}),
        )
        application.scoring_state = "QUEUED"
        application.revision += 1
        _audit(
            session,
            request,
            actor,
            application,
            "scoring.retry_requested",
            job_id=job.id,
            reason=body.reason,
            changes={"state": "QUEUED"},
        )
        result = {"job_id": str(job.id), "application_revision": application.revision}
    return JSONResponse(status_code=202, content={"data": result})


# --- Reviews and decisions ------------------------------------------------------


def _load_review(session: Session, application: Application, review_id: UUID) -> Review:
    review = session.get(Review, review_id)
    if review is None or review.application_id != application.id:
        raise AppError(404, "NOT_FOUND", "Review not found")
    return review


@router.post(
    "/applications/{application_id}/reviews", status_code=201, dependencies=[Depends(no_query)]
)
def start_review(application_id: UUID, body: ReviewStartInput, request: Request) -> JSONResponse:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST"}, mutation=True)
        application = _load_application(session, actor, application_id, lock=True)
        _check_revision(application, body.expected_revision)
        if application.review_state != "ASSIGNED" or application.assignee_id != actor.id:
            raise AppError(409, "INVALID_STATE", "Only the assigned Analyst can start a review")
        prediction = _prediction(session, application.id)
        if application.scoring_state != "SUCCEEDED" or prediction is None:
            raise AppError(409, "INVALID_STATE", "A successful prediction is required")
        number = (
            session.scalar(
                select(func.max(Review.review_number)).where(
                    Review.application_id == application.id
                )
            )
            or 0
        ) + 1
        review = Review(
            application_id=application.id,
            author_id=actor.id,
            review_number=number,
            prediction_id=prediction.id,
        )
        session.add(review)
        application.review_state = "IN_REVIEW"
        application.revision += 1
        session.flush()
        _audit(
            session,
            request,
            actor,
            application,
            "review.started",
            changes={"state": "IN_REVIEW", "review_id": str(review.id)},
        )
        result = {"application_revision": application.revision, "review": _review_out(review)}
    return JSONResponse(status_code=201, content={"data": result})


@router.patch(
    "/applications/{application_id}/reviews/{review_id}", dependencies=[Depends(no_query)]
)
def patch_review(
    application_id: UUID, review_id: UUID, body: ReviewPatchInput, request: Request
) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST"}, mutation=True)
        application = _load_application(session, actor, application_id, lock=True)
        _check_revision(application, body.expected_revision)
        review = _load_review(session, application, review_id)
        if (
            application.review_state != "IN_REVIEW"
            or review != _current_review(session, application)
            or review.submitted_at is not None
            or review.author_id != actor.id
            or application.assignee_id != actor.id
        ):
            raise AppError(409, "INVALID_STATE", "Only the assigned Analyst can edit this draft")
        review.notes = body.notes
        review.proposal = body.proposal
        application.revision += 1
        _audit(
            session,
            request,
            actor,
            application,
            "review.draft_saved",
            changes={
                "state": "IN_REVIEW",
                "review_id": str(review.id),
                "proposal": review.proposal,
            },
        )
        result = {"application_revision": application.revision, "review": _review_out(review)}
    return Envelope(data=result)


@router.post(
    "/applications/{application_id}/reviews/{review_id}/submit", dependencies=[Depends(no_query)]
)
def submit_review(
    application_id: UUID, review_id: UUID, body: ReviewStartInput, request: Request
) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST"}, mutation=True)
        application = _load_application(session, actor, application_id, lock=True)
        _check_revision(application, body.expected_revision)
        review = _load_review(session, application, review_id)
        if (
            application.review_state != "IN_REVIEW"
            or review != _current_review(session, application)
            or review.submitted_at is not None
            or review.author_id != actor.id
            or application.assignee_id != actor.id
        ):
            raise AppError(409, "INVALID_STATE", "Only the assigned Analyst can submit this draft")
        if not review.notes.strip() or review.proposal is None:
            raise AppError(422, "VALIDATION_ERROR", "Submit requires notes and a proposal")
        prediction = _prediction(session, application.id)
        if prediction is None or review.prediction_id != prediction.id:
            raise AppError(409, "INVALID_STATE", "A valid prediction is required")
        review.submitted_at = now_utc()
        application.review_state = "PENDING_DECISION"
        application.revision += 1
        _audit(
            session,
            request,
            actor,
            application,
            "review.submitted",
            changes={"state": "PENDING_DECISION", "review_id": str(review.id)},
        )
        result = {"application_revision": application.revision, "review": _review_out(review)}
    return Envelope(data=result)


@router.post("/applications/{application_id}/return", dependencies=[Depends(no_query)])
def return_review(
    application_id: UUID, body: ReturnInput, request: Request
) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"}, mutation=True)
        application = _load_application(session, actor, application_id, lock=True)
        _check_revision(application, body.expected_revision)
        if application.review_state != "PENDING_DECISION":
            raise AppError(409, "INVALID_STATE", "Only a pending submission can be returned")
        current = _current_review(session, application)
        if current is None or current.id != body.review_id:
            raise AppError(409, "INVALID_STATE", "Review is not the current submission")
        assignee = session.scalar(
            select(User)
            .where(User.id == application.assignee_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if assignee is None or not assignee.is_active or assignee.role != "ANALYST":
            raise AppError(
                409, "INVALID_STATE", "Reassign to an active Analyst before returning the review"
            )
        draft = Review(
            application_id=application.id,
            author_id=assignee.id,
            review_number=current.review_number + 1,
            prediction_id=current.prediction_id,
        )
        session.add(draft)
        application.review_state = "IN_REVIEW"
        application.revision += 1
        session.flush()
        _audit(
            session,
            request,
            actor,
            application,
            "review.returned",
            reason=body.reason,
            changes={"state": "IN_REVIEW", "review_id": str(draft.id)},
        )
        result = _detail(session, application)
    return Envelope(data=result)


@router.post(
    "/applications/{application_id}/decisions", status_code=201, dependencies=[Depends(no_query)]
)
def create_decision(
    application_id: UUID,
    body: DecisionInput,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    if idempotency_key is None or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", idempotency_key) is None:
        raise AppError(422, "VALIDATION_ERROR", "A valid Idempotency-Key header is required")
    payload = {
        "expected_revision": body.expected_revision,
        "review_id": str(body.review_id),
        "outcome": body.outcome,
        "reason": body.reason.strip(),
    }
    payload_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
    with transaction(request) as session:
        actor = authenticate(session, request, {"MANAGER"}, mutation=True)
        application = _load_application(session, actor, application_id, lock=True)
        prior = session.get(
            DecisionIdempotency, (actor.id, "decisions.create", application.id, idempotency_key)
        )
        if prior is not None:
            if prior.payload_sha256 != payload_hash:
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "Key was used with different input")
            return JSONResponse(status_code=201, content={"data": prior.response})
        _check_revision(application, body.expected_revision)
        if application.review_state != "PENDING_DECISION":
            raise AppError(409, "INVALID_STATE", "Only a pending submission can be decided")
        current = _current_review(session, application)
        if current is None or current.id != body.review_id or current.submitted_at is None:
            raise AppError(409, "INVALID_STATE", "Review is not the current submission")
        # Separation of duties covers drafts too: anyone who ever reviewed
        # this application is barred from deciding it, even after a role change.
        reviewed = session.scalar(
            select(func.count())
            .select_from(Review)
            .where(Review.application_id == application.id, Review.author_id == actor.id)
        )
        if reviewed:
            raise AppError(403, "SEPARATION_OF_DUTIES", "A reviewer cannot decide this application")
        prediction = session.get(Prediction, current.prediction_id)
        if prediction is None:
            raise AppError(409, "INVALID_STATE", "A valid prediction is required")
        decision = Decision(
            application_id=application.id,
            review_id=current.id,
            prediction_id=prediction.id,
            decided_by=actor.id,
            outcome=body.outcome,
            reason=body.reason.strip(),
        )
        session.add(decision)
        application.review_state = "DECIDED"
        application.revision += 1
        session.flush()
        result: dict[str, Any] = {
            "application_revision": application.revision,
            "decision": {
                "id": str(decision.id),
                "application_id": str(decision.application_id),
                "review_id": str(decision.review_id),
                "prediction_id": str(decision.prediction_id),
                "decided_by": str(decision.decided_by),
                "outcome": decision.outcome,
                "reason": decision.reason,
                "created_at": _ts(decision.created_at),
            },
        }
        session.add(
            DecisionIdempotency(
                actor_id=actor.id,
                operation="decisions.create",
                application_id=application.id,
                key=idempotency_key,
                payload_sha256=payload_hash,
                decision_id=decision.id,
                status=201,
                response=result,
            )
        )
        _audit(
            session,
            request,
            actor,
            application,
            "decision.created",
            reason=body.reason.strip(),
            changes={"state": "DECIDED", "outcome": decision.outcome},
        )
    return JSONResponse(status_code=201, content={"data": result})


# --- Reports --------------------------------------------------------------------


@router.get("/reports/workload", dependencies=[Depends(no_query)])
def workload(request: Request) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ANALYST", "MANAGER"})

        def counts(column: Any) -> list[dict[str, Any]]:
            statement = select(column, func.count()).group_by(column).order_by(column)
            if actor.role == "ANALYST":
                statement = statement.where(Application.assignee_id == actor.id)
            return [
                {"value": str(value) if value is not None else None, "count": int(count)}
                for value, count in session.execute(statement).all()
            ]

        total_statement = select(func.count()).select_from(Application)
        if actor.role == "ANALYST":
            total_statement = total_statement.where(Application.assignee_id == actor.id)
        result = {
            "total": session.scalar(total_statement) or 0,
            "by_review_state": counts(Application.review_state),
            "by_scoring_state": counts(Application.scoring_state),
            "by_assignee": counts(Application.assignee_id),
        }
    return Envelope(data=result)


@router.get("/models/current", dependencies=[Depends(no_query)])
def current_model(request: Request) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        authenticate(session, request, {"ANALYST", "MANAGER"})
        release = request.app.state.release
        if release is None:
            raise AppError(503, "MODEL_UNAVAILABLE", "No pinned model release is configured")
        result = {
            "model_version": release.model_version,
            "model_sha256": release.model_sha256,
            "feature_contract_version": release.feature_contract_version,
            "model_run_id": release.model_run_id,
            "lock_id": release.lock_id,
            "limitations": LIMITATIONS,
        }
    return Envelope(data=result)


@router.get("/models/comparison", dependencies=[Depends(no_query)])
def model_comparison(request: Request) -> Envelope[dict[str, Any]]:
    with transaction(request) as session:
        authenticate(session, request, {"ANALYST", "MANAGER"})
        comparison = request.app.state.comparison
        if comparison is None:
            raise AppError(404, "NOT_FOUND", "No comparison report is configured")
        result = {**comparison, "limitations": LIMITATIONS}
    return Envelope(data=result)
