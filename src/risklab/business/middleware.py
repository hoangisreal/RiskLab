"""Bound requests before parsing; emit metadata-only JSON and no-store responses."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOGGER = logging.getLogger("risklab.business")


class BusinessMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        status = 500
        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                message.setdefault("headers", []).extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
            await send(message)

        async def reject(status_code: int, code: str) -> None:
            response = JSONResponse(
                {"error": {"code": code, "message": code, "request_id": request_id, "details": []}},
                status_code=status_code,
            )
            await response(scope, receive, tracked_send)

        try:
            cap = 1_114_112 if scope["path"] == "/api/v1/imports" else 65_536
            headers = dict(scope.get("headers", []))
            try:
                length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                await reject(422, "VALIDATION_ERROR")
                return
            if length < 0:
                await reject(422, "VALIDATION_ERROR")
                return
            if length > cap:
                await reject(413, "PAYLOAD_TOO_LARGE")
                return
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > cap:
                    await reject(413, "PAYLOAD_TOO_LARGE")
                    return
                if not message.get("more_body", False):
                    break
            consumed = False

            async def buffered_receive() -> Message:
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            try:
                await self.app(scope, buffered_receive, tracked_send)
            except Exception:
                if response_started:
                    raise
                await reject(500, "INTERNAL_ERROR")
        finally:
            # Do not log path/query values, headers or request bodies: all are untrusted.
            LOGGER.info(
                json.dumps(
                    {
                        "service": "api",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "level": "INFO",
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": getattr(scope.get("route"), "path", "unmatched"),
                        "actor_id": scope["state"].get("actor_id"),
                        "status": status,
                        "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    }
                )
            )
