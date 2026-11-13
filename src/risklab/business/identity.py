"""Local accounts, digest-only sessions and server-side authorization.

Identity mutations take a short transaction advisory lock before acquiring rows.
This serializes last-admin checks and prevents actor/target lock-order deadlocks.
Business services authenticate again inside their own mutation transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from risklab.business.errors import AppError
from risklab.business.models import LoginSession, User, now_utc
from risklab.business.settings import Settings

HASHER = PasswordHasher(time_cost=3, memory_cost=19456, parallelism=1)
DUMMY_PASSWORD_HASH = HASHER.hash(secrets.token_urlsafe(32))
IDENTITY_LOCK = 651274001


def identity_lock(session: Session, *, shared: bool = False) -> None:
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    session.execute(text(f"SELECT {function}(:key)"), {"key": IDENTITY_LOCK})


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_name(settings: Settings) -> str:
    return (
        "__Host-risklab_session" if settings.environment == "production" else "risklab_dev_session"
    )


def set_cookie(response: Response, settings: Settings, token: str, seconds: int) -> None:
    response.set_cookie(
        cookie_name(settings),
        token,
        max_age=seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )


def find_session(session: Session, request: Request) -> LoginSession | None:
    token = request.cookies.get(cookie_name(request.app.state.settings), "")
    if not 32 <= len(token) <= 128:
        return None
    # Refresh even if authenticate() already read this token before waiting on
    # an account lock: password reset may have revoked it in the meantime.
    record = session.scalar(
        select(LoginSession)
        .where(LoginSession.token_hash == digest(token))
        .execution_options(populate_existing=True)
    )
    return record if record is not None and record.expires_at > now_utc() else None


def csrf_check(session: Session, request: Request) -> LoginSession:
    settings: Settings = request.app.state.settings
    # An absent Origin is rejected for browser mutations as well as a hostile one.
    if request.headers.get("origin") != settings.origin:
        raise AppError(403, "CSRF_FAILED", "Invalid request origin")
    record = find_session(session, request)
    token = request.headers.get("x-csrf-token", "")
    if (
        record is None
        or not 32 <= len(token) <= 128
        or not hmac.compare_digest(record.csrf_hash, digest(token))
    ):
        raise AppError(403, "CSRF_FAILED", "Refresh the session and retry")
    return record


def authenticate(
    session: Session, request: Request, roles: set[str], *, mutation: bool = False
) -> User:
    record = find_session(session, request)
    if record is None or record.user_id is None:
        raise AppError(401, "AUTHENTICATION_REQUIRED", "Sign in to continue")
    query = select(User).where(User.id == record.user_id)
    if mutation:
        # Hold role/status stable until this mutation commits; account changes wait.
        query = query.with_for_update(read=True)
    user = session.scalar(query)
    if user is None or not user.is_active:
        raise AppError(401, "AUTHENTICATION_REQUIRED", "Sign in to continue")
    request.state.actor_id = str(user.id)
    if user.role not in roles:
        raise AppError(403, "FORBIDDEN", "This action is not permitted")
    if mutation:
        csrf_check(session, request)
    return user


def ensure_application_access(user: User, assignee_id: UUID | None) -> None:
    if user.role == "MANAGER" or (user.role == "ANALYST" and user.id == assignee_id):
        return
    if user.role == "ADMIN":
        raise AppError(403, "FORBIDDEN", "Business access is not permitted")
    raise AppError(404, "NOT_FOUND", "Application not found")


def new_session(session: Session, user_id: UUID | None) -> tuple[str, str, int]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    seconds = 8 * 3600 if user_id else 15 * 60
    session.add(
        LoginSession(
            token_hash=digest(token),
            user_id=user_id,
            csrf_hash=digest(csrf),
            expires_at=now_utc() + timedelta(seconds=seconds),
        )
    )
    return token, csrf, seconds


def verify_password(stored: str, password: str) -> bool:
    try:
        return bool(HASHER.verify(stored, password))
    except (VerificationError, InvalidHashError):
        return False


def revoke_sessions(session: Session, user_id: UUID) -> None:
    session.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
