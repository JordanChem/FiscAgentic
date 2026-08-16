"""
Application FastAPI — assistant fiscal.

Consommée par l'API de fiscalonline.fr, qui joue le rôle de proxy
d'authentification : elle authentifie l'abonné puis relaie l'appel avec un
secret partagé et l'identité de l'utilisateur en en-têtes.

Lancement local :
    uvicorn api.main:app --reload --port 8080

Production (cf. deploy/) :
    gunicorn api.main:app -k uvicorn.workers.UvicornWorker -w 2 \
        -b 127.0.0.1:8080 --timeout 900 --graceful-timeout 60
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.errors import register_exception_handlers
from api.logging_conf import configure_logging
from api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from api.routes import chat, conversations, feedback, health, meta
from api.runner import shutdown_pool
from api.settings import get_settings

load_dotenv()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    missing = settings.missing_required()
    if missing:
        # On échoue vite et bruyamment plutôt qu'à la quatrième minute d'un
        # pipeline : la configuration incomplète est visible au démarrage.
        message = f"Configuration incomplète — variables manquantes : {', '.join(missing)}"
        if settings.environment == "prod":
            raise RuntimeError(message)
        logger.warning("%s (toléré hors production)", message)

    logger.info("Démarrage %s (%s) — %d pipelines simultanés max, protocole AI SDK %s",
                settings.app_name, settings.environment,
                settings.max_concurrent_pipelines, settings.ai_sdk_protocol)
    try:
        yield
    finally:
        logger.info("Arrêt — attente des pipelines en cours…")
        shutdown_pool(wait=True)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Assistant Fiscal — API",
        version="1.0.0",
        description="Pipeline multi-agents de réponse aux questions fiscales françaises.",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "prod" else None,
        redoc_url=None,
    )

    # Ordre d'ajout inversé à l'exécution : la limite de taille s'applique
    # d'abord, puis le contexte de requête enveloppe le tout.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestContextMiddleware)

    origins = settings.cors_origin_list
    if origins:
        # Jamais "*" : un secret partagé transite en en-tête.
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-API-Key", "X-User-Email", "X-User-Id",
                           "X-Request-Id"],
        )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(feedback.router)
    app.include_router(meta.router)
    return app


app = create_app()
