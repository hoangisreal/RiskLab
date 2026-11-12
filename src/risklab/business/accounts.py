"""Account HTTP endpoints with explicit commits, safe DTOs and audit boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from risklab.business.audit import append_audit
from risklab.business.db import session_factory
from risklab.business.errors import AppError
from risklab.business.identity import (
    DUMMY_PASSWORD_HASH,
    HASHER,
    authenticate,
    cookie_name,
    csrf_check,
    digest,
    find_session,
    identity_lock,
    new_session,
    revoke_sessions,
    set_cookie,
    verify_password,
)
from risklab.business.models import AuditEvent, LoginLimit, LoginSession, User, now_utc
from risklab.business.schemas import (
    AuditQuery,
    CreateUserInput,
    Envelope,
    LoginInput,
    Page,
    PaginationQuery,
    ResetPasswordInput,
    UserOut,
    UserPatch,
    UserQuery,
)

router = APIRouter(prefix="/api/v1")


def no_query(request: Request) -> None:
    if request.query_params:
        raise AppError(422, "VALIDATION_ERROR", "Query parameters are not accepted here")


@contextmanager
def transaction(request: Request) -> Iterator[Session]:
    engine = request.app.state.engine
    if engine is None:
        raise AppError(503, "DEPENDENCY_UNAVAILABLE", "Application database is not ready")
    with session_factory(engine).begin() as session:
        yield session


def audit_account(
    session: Session,
    request: Request,
    actor: User,
    target: User,
    action: str,
    reason: str | None = None,
    changes: dict[str, str | int | bool | None] | None = None,
) -> None:
    request.state.actor_id = str(actor.id)
    append_audit(
        session,
        action=action,
        actor_id=actor.id,
        resource_type="account",
        resource_id=target.id,
        request_id=UUID(request.state.request_id),
        reason=reason,
        changes=changes,
    )


def rate_allowed(session: Session, ip: str, username: str) -> bool:
    # Identity lock already serializes these short operations; no process-local bypass.
    cutoff = now_utc() - timedelta(minutes=5)
    expired = select(LoginLimit.key_hash).where(LoginLimit.window_start < cutoff).limit(100)
    session.execute(delete(LoginLimit).where(LoginLimit.key_hash.in_(expired)))
    for key in ("ip:" + ip, "username:" + username):
        hashed = digest(key)
        record = session.get(LoginLimit, hashed)
        if record is not None and record.window_start < cutoff:
            session.delete(record)
            session.flush()
            record = None
        if record is None:
            record = LoginLimit(key_hash=hashed, window_start=now_utc(), attempts=1)
            session.add(record)
        elif record.attempts >= 10:
            return False
        else:
            record.attempts += 1
    return True


@router.get("/auth/csrf", dependencies=[Depends(no_query)])
def csrf(request: Request, response: Response) -> Envelope[dict[str, str]]:
    import secrets

    with transaction(request) as session:
        identity_lock(session)
        expired = (
            select(LoginSession.token_hash).where(LoginSession.expires_at <= now_utc()).limit(100)
        )
        session.execute(delete(LoginSession).where(LoginSession.token_hash.in_(expired)))
        record = find_session(session, request)
        if record is not None and record.user_id is not None:
            user = session.get(User, record.user_id)
            if user is None or not user.is_active:
                session.delete(record)
                record = None
        if record is None:
            token, csrf_token, seconds = new_session(session, None)
            set_cookie(response, request.app.state.settings, token, seconds)
        else:
            # Rotating CSRF on bootstrap/reload avoids retaining a reversible CSRF secret in DB.
            csrf_token = secrets.token_urlsafe(32)
            record.csrf_hash = digest(csrf_token)
    return Envelope(data={"csrf_token": csrf_token})


@router.post("/auth/login", dependencies=[Depends(no_query)])
def login(body: LoginInput, request: Request, response: Response) -> Envelope[dict[str, Any]]:
    result: UserOut | None = None
    allowed = False
    with transaction(request) as session:
        identity_lock(session)
        bootstrap = csrf_check(session, request)
        allowed = rate_allowed(
            session, request.client.host if request.client else "unknown", body.username
        )
        if allowed:
            user = session.scalar(select(User).where(User.username == body.username))
            stored = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
            valid = verify_password(stored, body.password.get_secret_value())
            if valid and user is not None and user.is_active:
                if HASHER.check_needs_rehash(user.password_hash):
                    user.password_hash = HASHER.hash(body.password.get_secret_value())
                session.delete(bootstrap)
                token, csrf_token, seconds = new_session(session, user.id)
                set_cookie(response, request.app.state.settings, token, seconds)
                audit_account(session, request, user, user, "session.login")
                result = UserOut.model_validate(user)
        # Failed attempts must COMMIT their counters, not roll back when returning 401/429.
    if not allowed:
        raise AppError(429, "RATE_LIMITED", "Too many attempts; retry later")
    if result is None:
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid credentials")
    return Envelope(data={"user": result.model_dump(mode="json"), "csrf_token": csrf_token})


@router.get("/auth/me", dependencies=[Depends(no_query)])
def me(request: Request) -> Envelope[UserOut]:
    with transaction(request) as session:
        actor = authenticate(session, request, {"ADMIN", "MANAGER", "ANALYST"})
        result = UserOut.model_validate(actor)
    return Envelope(data=result)


@router.post("/auth/logout", status_code=204, dependencies=[Depends(no_query)])
def logout(request: Request) -> Response:
    with transaction(request) as session:
        identity_lock(session)
        actor = authenticate(session, request, {"ADMIN", "MANAGER", "ANALYST"}, mutation=True)
        record = find_session(session, request)
        session.delete(record)
        audit_account(session, request, actor, actor, "session.logout")
    response = Response(status_code=204)
    response.delete_cookie(
        cookie_name(request.app.state.settings),
        path="/",
        secure=request.app.state.settings.environment == "production",
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/admin/users")
def users(request: Request, query: Annotated[UserQuery, Query()]) -> Page[UserOut]:
    with transaction(request) as session:
        authenticate(session, request, {"ADMIN"})
        statement = select(User)
        if query.q is not None:
            statement = statement.where(User.username.contains(query.q, autoescape=True))
        if query.role is not None:
            statement = statement.where(User.role == query.role)
        if query.is_active is not None:
            statement = statement.where(User.is_active == query.is_active)
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = session.scalars(
            statement.order_by(User.created_at.desc(), User.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        result = [UserOut.model_validate(user) for user in rows]
    return Page(
        data=result, pagination={"page": query.page, "page_size": query.page_size, "total": total}
    )


@router.post("/admin/users", status_code=201, dependencies=[Depends(no_query)])
def create_user(body: CreateUserInput, request: Request) -> Envelope[UserOut]:
    try:
        with transaction(request) as session:
            identity_lock(session)
            actor = authenticate(session, request, {"ADMIN"}, mutation=True)
            user = User(
                username=body.username,
                password_hash=HASHER.hash(body.password.get_secret_value()),
                role=body.role,
            )
            session.add(user)
            session.flush()
            audit_account(
                session, request, actor, user, "user.created", changes={"role": user.role}
            )
            result = UserOut.model_validate(user)
    except IntegrityError as error:
        original = error.orig
        constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
        if getattr(original, "sqlstate", None) == "23505" and constraint == "users_username_key":
            raise AppError(409, "RESOURCE_CONFLICT", "Username already exists") from None
        raise
    return Envelope(data=result)


def target_user(session: Session, user_id: UUID, revision: int) -> User:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise AppError(404, "NOT_FOUND", "Account not found")
    if user.revision != revision:
        raise AppError(409, "REVISION_CONFLICT", "Account changed; reload before retrying")
    return user


@router.patch("/admin/users/{user_id}", dependencies=[Depends(no_query)])
def update_user(user_id: UUID, body: UserPatch, request: Request) -> Envelope[UserOut]:
    with transaction(request) as session:
        identity_lock(session)
        actor = authenticate(session, request, {"ADMIN"}, mutation=True)
        user = target_user(session, user_id, body.expected_revision)
        if actor.id == user.id and body.role is not None and body.role != user.role:
            raise AppError(403, "FORBIDDEN", "Cannot change your own role")
        removes_admin = (
            user.role == "ADMIN"
            and user.is_active
            and (body.is_active is False or (body.role is not None and body.role != "ADMIN"))
        )
        if removes_admin:
            count = session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "ADMIN", User.is_active.is_(True))
            )
            if count == 1:
                raise AppError(409, "LAST_ADMIN", "Cannot remove the last active Admin")
        if body.role is not None:
            user.role = body.role
        if body.is_active is not None:
            user.is_active = body.is_active
        user.revision += 1
        revoke_sessions(session, user.id)
        audit_account(
            session,
            request,
            actor,
            user,
            "user.changed",
            reason=body.reason,
            changes={"role": user.role, "is_active": user.is_active, "revision": user.revision},
        )
        result = UserOut.model_validate(user)
    return Envelope(data=result)


@router.post(
    "/admin/users/{user_id}/reset-password", status_code=204, dependencies=[Depends(no_query)]
)
def reset_password(user_id: UUID, body: ResetPasswordInput, request: Request) -> Response:
    with transaction(request) as session:
        identity_lock(session)
        actor = authenticate(session, request, {"ADMIN"}, mutation=True)
        user = target_user(session, user_id, body.expected_revision)
        user.password_hash = HASHER.hash(body.new_password.get_secret_value())
        user.revision += 1
        revoke_sessions(session, user.id)
        audit_account(
            session,
            request,
            actor,
            user,
            "user.password_reset",
            reason=body.reason,
            changes={"revision": user.revision},
        )
    return Response(status_code=204)


@router.get("/analysts")
def analysts(
    request: Request,
    filters: Annotated[PaginationQuery, Query()],
) -> Page[dict[str, str]]:
    with transaction(request) as session:
        authenticate(session, request, {"MANAGER"})
        query = select(User).where(User.role == "ANALYST", User.is_active.is_(True))
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = session.scalars(
            query.order_by(User.created_at.desc(), User.id.desc())
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        result = [{"id": str(row.id), "username": row.username} for row in rows]
    return Page(
        data=result,
        pagination={"page": filters.page, "page_size": filters.page_size, "total": total},
    )


@router.get("/admin/audit-events")
def account_audit(
    request: Request,
    filters: Annotated[AuditQuery, Query()],
) -> Page[dict[str, Any]]:
    with transaction(request) as session:
        authenticate(session, request, {"ADMIN"})
        query = select(AuditEvent).where(AuditEvent.resource_type == "account")
        if filters.user_id is not None:
            query = query.where(AuditEvent.resource_id == filters.user_id)
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = session.scalars(
            query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        result = [
            {
                "id": str(row.id),
                "actor_id": str(row.actor_id) if row.actor_id else None,
                "actor_type": row.actor_type,
                "action": row.action,
                "resource_id": str(row.resource_id),
                "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
                "request_id": str(row.request_id) if row.request_id else None,
                "job_id": None,
                "reason": row.reason,
                "metadata": row.details,
            }
            for row in rows
        ]
    return Page(
        data=result,
        pagination={"page": filters.page, "page_size": filters.page_size, "total": total},
    )
