"""FastAPI app: /predict, /models/current, /health, /monitoring/evaluate, reload."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from risklab.api.model_store import ModelStore
from risklab.api.schemas import MonitoringRequest, PredictRequest
from risklab.api.service import ServiceError, predict
from risklab.features.registry import load_registry


def create_app(
    *,
    store: ModelStore | None = None,
    root: Path | None = None,
    config_dir: Path | None = None,
    monitoring_batches: dict[str, Any] | None = None,
) -> Any:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse

    root = root or Path.cwd()
    config_dir = config_dir or (root / "configs")
    store = store or ModelStore()
    registry = load_registry(config_dir)
    app = FastAPI(title="RiskLab", version="1.0")
    app.state.store = store
    app.state.monitoring_batches = monitoring_batches or {}

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "ready": store.ready,
            "model_version": store.current.version if store.current else None,
        }

    @app.get("/models/current")
    def current() -> dict[str, Any]:
        if store.current is None:
            raise HTTPException(status_code=503, detail="no model loaded")
        c = store.current
        return {
            "model_version": c.version,
            "run_id": c.run_id,
            "lock_id": c.lock_id,
            "threshold": c.threshold,
            "contract_version": c.contract_version,
            "feature_groups": c.feature_groups,
        }

    @app.post("/predict")
    def do_predict(req: PredictRequest) -> Any:
        return predict(req, store=store, registry=registry, root=root, config_dir=config_dir)

    @app.post("/monitoring/evaluate")
    def monitoring(req: MonitoringRequest) -> dict[str, Any]:
        batch = app.state.monitoring_batches.get(req.batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"unknown batch_id {req.batch_id!r}")
        return dict(batch)

    @app.post("/admin/models/reload")
    def reload_model(x_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
        expected = os.environ.get("RISKLAB_ADMIN_API_KEY", "")
        if not expected or x_admin_key != expected:
            raise HTTPException(status_code=403, detail="invalid admin key")
        # Reload resolves the champion alias; failure keeps the current model serving.
        try:
            from risklab.registry.client import resolve_champion

            info = resolve_champion()
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"reload failed, previous model kept: {exc}"
            ) from exc
        return {"reloaded": True, **info}

    return app


app = create_app()

__all__ = ["app", "create_app"]
