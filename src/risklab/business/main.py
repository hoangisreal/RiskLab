"""Versioned business API; deliberately does not mount the unauthenticated legacy ML API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from starlette.exceptions import HTTPException

from risklab.business.accounts import router as accounts_router
from risklab.business.catalog import DemoCatalog, PinnedRelease
from risklab.business.db import make_engine, schema_ready
from risklab.business.errors import AppError, error_response
from risklab.business.features import router as features_router
from risklab.business.logging import configure_logging
from risklab.business.middleware import BusinessMiddleware
from risklab.business.settings import Settings, load_settings
from risklab.business.storage import LocalStorage


def _load_catalog(settings: Settings) -> DemoCatalog | None:
    if settings.catalog_file is None or settings.catalog_sha256 is None:
        return None
    try:
        return DemoCatalog(
            settings.catalog_file.read_bytes(),
            expected_sha256=settings.catalog_sha256,
            config_dir=settings.config_dir,
        )
    except Exception:
        return None  # Import endpoints report dependency unavailable, never guess.


def _load_release(settings: Settings) -> PinnedRelease | None:
    if settings.release_file is None:
        return None
    try:
        return PinnedRelease.model_validate_json(settings.release_file.read_bytes())
    except Exception:
        return None


def _load_comparison(settings: Settings) -> dict[str, Any] | None:
    if settings.comparison_file is None:
        return None
    try:
        import json

        value = json.loads(settings.comparison_file.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def create_app(
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    catalog: DemoCatalog | None = None,
    release: PinnedRelease | None = None,
    storage: LocalStorage | None = None,
    comparison: dict[str, Any] | None = None,
) -> FastAPI:
    configure_logging()
    settings = settings or load_settings()
    database = engine if engine is not None else make_engine(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is None and database is not None:
            database.dispose()

    app = FastAPI(title="RiskLab Business API", version="1", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = database
    app.state.catalog = catalog if catalog is not None else _load_catalog(settings)
    app.state.release = release if release is not None else _load_release(settings)
    app.state.storage = storage if storage is not None else LocalStorage(settings.storage_root)
    app.state.comparison = comparison if comparison is not None else _load_comparison(settings)
    app.add_middleware(BusinessMiddleware)
    app.include_router(accounts_router)
    app.include_router(features_router)

    @app.exception_handler(AppError)
    async def business_error(request: Request, error: AppError) -> JSONResponse:
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(map(str, item["loc"])), "code": item["type"]}
            for item in error.errors()[:100]
        ]
        return error_response(request, 422, "VALIDATION_ERROR", "Invalid request", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = "NOT_FOUND" if error.status_code == 404 else "REQUEST_REJECTED"
        return error_response(request, error.status_code, code, code)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        # Never forward exception strings or traceback locals to a client/log.
        return error_response(request, 500, "INTERNAL_ERROR", "Request could not be completed")

    @app.get("/api/v1/health/live")
    def live() -> dict[str, Any]:
        return {"data": {"status": "ok"}}

    @app.get("/api/v1/health/ready")
    def ready() -> dict[str, Any]:
        if not schema_ready(database):
            raise AppError(503, "DEPENDENCY_UNAVAILABLE", "Application database is not ready")
        return {"data": {"status": "ready"}}

    return app
