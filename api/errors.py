"""
Contrat d'erreur uniforme.

Toutes les réponses non-SSE en échec ont la même forme :

    {"error": {"code": "...", "message": "...", "request_id": "...",
               "trace_id": "...", "retriable": true}}

Aucune trace d'exécution (traceback) n'est jamais renvoyée au client : l'app
Streamlit affichait `traceback.format_exc()` à l'écran, ce comportement ne doit
pas survivre dans un service exposé.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.logging_conf import current_request_id, current_trace_id

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Erreur métier destinée au client."""

    def __init__(self, status_code: int, code: str, message: str,
                 retriable: bool = False, headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retriable = retriable
        self.headers = headers or {}
        super().__init__(message)

    def payload(self) -> Dict[str, Any]:
        return error_payload(self.code, self.message, self.retriable)


# ── Raccourcis ───────────────────────────────────────────────────────────────
def unauthorized(message: str = "Clé d'API invalide ou absente.") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", message)


def forbidden(message: str) -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "forbidden", message)


def not_found(message: str = "Ressource introuvable.") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "not_found", message)


def capacity_exceeded(retry_after: int = 30) -> ApiError:
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS, "capacity_exceeded",
        "Le service traite déjà le maximum de questions simultanées. Réessayez dans quelques instants.",
        retriable=True, headers={"Retry-After": str(retry_after)},
    )


def rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited",
        "Trop de questions sur la période. Réessayez plus tard.",
        retriable=True, headers={"Retry-After": str(retry_after)},
    )


def pipeline_timeout() -> ApiError:
    return ApiError(
        status.HTTP_504_GATEWAY_TIMEOUT, "pipeline_timeout",
        "Le traitement a dépassé le temps imparti.", retriable=True,
    )


def internal_error() -> ApiError:
    return ApiError(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error",
        "Une erreur interne est survenue.", retriable=True,
    )


def error_payload(code: str, message: str, retriable: bool = False) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": current_request_id(),
            "trace_id": current_trace_id(),
            "retriable": retriable,
        }
    }


# ── Handlers ─────────────────────────────────────────────────────────────────
def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        if exc.status_code >= 500:
            logger.error("ApiError %s : %s", exc.code, exc.message)
        return JSONResponse(exc.payload(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', ())[1:])}: {e.get('msg', '')}".strip(": ")
            for e in exc.errors()[:5]
        )
        # 422 en littéral : Starlette déprécie tour à tour ses constantes
        # (UNPROCESSABLE_ENTITY → UNPROCESSABLE_CONTENT), le code ne bouge pas.
        return JSONResponse(
            error_payload("validation_error", detail or "Requête invalide."),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        # Le détail part dans les logs (avec request_id), jamais dans la réponse.
        logger.exception("Exception non gérée : %s", exc)
        err = internal_error()
        return JSONResponse(err.payload(), status_code=err.status_code)
