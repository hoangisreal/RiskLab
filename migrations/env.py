"""Migration URL comes from a separate operator secret, never from checked-in configuration."""

from __future__ import annotations

from alembic import context

from risklab.business import models  # noqa: F401 - populate metadata for schema checks
from risklab.business.db import Base, make_engine
from risklab.business.settings import load_settings

config = context.config
connection = config.attributes.get("connection")
if connection is not None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    settings = load_settings()
    engine = make_engine(settings)
    if engine is None:
        raise RuntimeError("Set RISKLAB_DATABASE_URL using migration credentials")
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
