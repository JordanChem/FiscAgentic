"""Middlewares : identifiant de requête, journal d'accès, limite de taille."""
from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api.errors import error_payload
from api.logging_conf import bind_request, current_request_id, new_request_id

logger = logging.getLogger("api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attache un `request_id` à la requête, aux logs et à la réponse."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or new_request_id()
        bind_request(request_id, request.headers.get("x-user-email"))
        request.state.request_id = request_id

        started = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)

        response.headers["x-request-id"] = request_id
        logger.info(
            "%s %s → %s", request.method, request.url.path, response.status_code,
            extra={"http_status": response.status_code, "duration_ms": elapsed_ms,
                   "path": request.url.path, "method": request.method},
        )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejette les corps trop volumineux avant toute désérialisation."""

    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return JSONResponse(
                error_payload("validation_error",
                              f"Corps de requête trop volumineux (max {self.max_bytes} octets)."),
                status_code=413,
                headers={"x-request-id": current_request_id() or ""},
            )
        return await call_next(request)
