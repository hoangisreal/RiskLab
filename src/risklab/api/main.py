"""FastAPI app: /predict, /models/current, /health, /monitoring/evaluate, reload."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from risklab.api.config import load_api_config
from risklab.api.contract import request_model
from risklab.api.middleware import RequestMiddleware
from risklab.api.model_store import ModelStore
from risklab.api.schemas import MonitoringRequest, PredictRequest
from risklab.api.service import ServiceError, predict
from risklab.api.validation import validate_history_records
from risklab.features.registry import load_registry
from risklab.models.select import protocol_hashes


def create_app(
    *,
    store: ModelStore | None = None,
    root: Path | None = None,
    config_dir: Path | None = None,
    monitoring_batches: dict[str, Any] | None = None,
) -> Any:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    root = root or Path.cwd()
    config_dir = config_dir or (root / "configs")
    store = store or ModelStore()
    registry = load_registry(config_dir)
    app = FastAPI(title="RiskLab", version=registry.contract_version)
    app.add_middleware(RequestMiddleware, max_body_bytes=load_api_config(config_dir).max_body_bytes)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{k: row[k] for k in ("loc", "msg", "type")} for row in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": details})

    if store.current is None:
        import os as _os

        if _os.environ.get("MLFLOW_TRACKING_URI"):
            try:
                from risklab.api.model_store import load_from_registry

                load_from_registry(
                    store,
                    expected_contract=registry.contract_version,
                    expected_protocol=protocol_hashes(root),
                )
            except Exception:
                pass  # stay not-ready; /health reports it, reload can retry
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
        metadata = c.serving_config
        return {
            "model_version": c.version,
            "run_id": c.run_id,
            "lock_id": c.lock_id,
            "threshold": c.threshold,
            "contract_version": c.contract_version,
            "feature_groups": c.feature_groups,
            "calibration_method": metadata.calibration_method if metadata else None,
            "partition_hash": metadata.partition_hash if metadata else None,
            "data_manifest_hash": metadata.data_manifest_hash if metadata else None,
            "model_sha256": metadata.model_sha256 if metadata else None,
            "selection_metrics": metadata.selection_metrics if metadata else None,
            "selection_run_id": metadata.selection_run_id if metadata else None,
            "cost_assumptions": metadata.cost.model_dump() if metadata else None,
            "git_commit": metadata.git_commit if metadata else None,
            "purpose": metadata.purpose if metadata else None,
        }

    def do_predict(req: Any, request: Request) -> Any:
        dto = PredictRequest.model_validate(req.model_dump(exclude_none=True))
        loaded = store.current
        request_store = ModelStore()
        if loaded is not None:
            request_store.swap(loaded)
            validate_history_records(
                {
                    name: source.model_dump(exclude_none=True)
                    for name, source in dto.history.items()
                },
                loaded.feature_groups,
            )
        response = predict(
            dto, store=request_store, registry=registry, root=root, config_dir=config_dir
        )
        request.state.request_id = response.request_id
        request.state.model_version = response.model_version
        return response

    do_predict.__annotations__["req"] = request_model(config_dir)
    do_predict.__annotations__["request"] = Request
    app.post("/predict")(do_predict)

    @app.post("/monitoring/evaluate")
    def monitoring(req: MonitoringRequest) -> dict[str, Any]:
        from risklab.monitoring.service import BatchSpec, registered_batches, run_monitoring

        batches = app.state.monitoring_batches or registered_batches(root)
        batch = batches.get(req.batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"unknown batch_id {req.batch_id!r}")
        if not store.ready:
            raise HTTPException(status_code=503, detail="no model loaded")
        try:
            return run_monitoring(
                BatchSpec.model_validate(batch),
                store=store,
                config_dir=config_dir,
                root=root,
                model_version=req.model_version,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"monitoring failed: {exc}") from exc

    @app.post("/admin/models/reload")
    def reload_model(x_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
        expected = os.environ.get("RISKLAB_ADMIN_API_KEY", "")
        if not expected or x_admin_key != expected:
            raise HTTPException(status_code=403, detail="invalid admin key")
        # Reload resolves the champion alias; failure keeps the current model serving.
        try:
            from risklab.api.model_store import load_from_registry

            loaded = load_from_registry(
                store,
                expected_contract=registry.contract_version,
                expected_protocol=protocol_hashes(root),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"reload failed, previous model kept: {exc}"
            ) from exc
        return {"reloaded": True, "version": loaded.version, "run_id": loaded.run_id}

    return app


app = create_app()

__all__ = ["app", "create_app"]
