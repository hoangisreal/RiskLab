"""Bounded PostgreSQL pool and explicit transaction ownership for application services."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from risklab.business.settings import Settings

# Advance alongside an additive migration; readiness must not accept arbitrary schema heads.
SCHEMA_REVISION = "0004_workflow"


class Base(DeclarativeBase):
    pass


def make_engine(settings: Settings) -> Engine | None:
    if settings.database_url is None:
        return None
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.pool_size,
        max_overflow=0,
        pool_timeout=settings.pool_timeout,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": settings.pool_timeout},
    )


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Caller uses factory.begin(): successful mutation + audit commit together."""
    return sessionmaker(engine, expire_on_commit=False)


def schema_ready(engine: Engine | None) -> bool:
    if engine is None:
        return False
    try:
        with engine.connect() as connection:
            heads = connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            return list(heads) == [SCHEMA_REVISION]
    except Exception:
        # Deliberately do not emit DB exception strings: they may contain credentials/SQL.
        return False
