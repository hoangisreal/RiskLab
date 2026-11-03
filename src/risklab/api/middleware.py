"""Bound request bodies and log only request metadata, never payloads or admin headers."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

LOGGER = logging.getLogger("risklab.api")


class RequestMiddleware:
    def __init__(self, app: Any, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["request_id"] = str(uuid.uuid4())
        start = time.perf_counter()
        status = 500
        size = 0

        async def limited_receive() -> Any:
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.max_body_bytes:
                raise HTTPException(413, "request body exceeds configured byte limit")
            return message

        async def tracked_send(message: Any) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", state["request_id"].encode())
                )
            await send(message)

        try:
            headers = dict(scope.get("headers", []))
            try:
                length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                await JSONResponse({"detail": "invalid content-length"}, 400)(
                    scope, receive, tracked_send
                )
                return
            if length < 0 or length > self.max_body_bytes:
                await JSONResponse({"detail": "request body exceeds configured byte limit"}, 413)(
                    scope, receive, tracked_send
                )
                return
            await self.app(scope, limited_receive, tracked_send)
        finally:
            LOGGER.info(
                json.dumps(
                    {
                        "request_id": state["request_id"],
                        "model_version": state.get("model_version"),
                        "status": status,
                        "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                    }
                )
            )
