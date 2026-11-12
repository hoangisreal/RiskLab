"""Operator-only account bootstrap. No default password or implicit sample accounts."""

from __future__ import annotations

import getpass

import typer
from pydantic import SecretStr
from sqlalchemy import func, select

from risklab.business.audit import append_audit
from risklab.business.db import make_engine, schema_ready, session_factory
from risklab.business.identity import HASHER, identity_lock
from risklab.business.models import User
from risklab.business.schemas import LoginInput
from risklab.business.settings import load_settings

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """RiskLab application operator commands; never use these as public HTTP endpoints."""


@app.command("bootstrap-admin")
def bootstrap_admin(username: str) -> None:
    """Create the first Admin using a hidden prompt, with a transaction and SYSTEM audit."""
    settings = load_settings()
    engine = make_engine(settings)
    if engine is None or not schema_ready(engine):
        raise typer.BadParameter("Migrate the application database before bootstrapping")
    password = getpass.getpass("New Admin password (hidden): ")
    confirmation = getpass.getpass("Confirm password (hidden): ")
    if password != confirmation:
        raise typer.BadParameter("Passwords do not match")
    try:
        credentials = LoginInput(username=username, password=SecretStr(password))
    except ValueError:
        raise typer.BadParameter("Invalid username or password length") from None
    try:
        with session_factory(engine).begin() as session:
            identity_lock(session)
            count = session.scalar(select(func.count()).select_from(User))
            if count:
                raise typer.BadParameter("Bootstrap is only allowed on an empty account database")
            user = User(
                username=credentials.username,
                role="ADMIN",
                password_hash=HASHER.hash(credentials.password.get_secret_value()),
            )
            session.add(user)
            session.flush()
            append_audit(
                session,
                action="user.bootstrapped",
                resource_type="account",
                resource_id=user.id,
                actor_type="SYSTEM",
                changes={"role": "ADMIN"},
            )
        typer.echo("Initial Admin created. No password was logged.")
    finally:
        engine.dispose()
