"""
Endpoint de conversation.

`POST /v1/chat` est un flux SSE au format AI SDK ; `POST /v1/chat/sync` rend la
même chose en un seul JSON (batch, debug, intégrations tierces).

Le flux est produit par un générateur **asynchrone** : c'est la seule forme qui
reçoit le `CancelledError` d'uvicorn à la déconnexion du client — condition de
l'annulation du pipeline (cf. `api/runner.py`).
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.deps import Principal, get_rate_limiter, require_principal
from api.errors import ApiError, forbidden, not_found, pipeline_timeout
from api.logging_conf import bind_conversation, bind_trace, current_request_id
from api.runner import PipelineSlot, stream_events
from api.schemas import ChatRequest, ChatSyncResponse
from api.settings import Settings, get_settings
from api.sse import SSEEncoder, sse_headers
from pipeline.errors import PipelineDeadlineExceeded
from pipeline.events import ResultEvent, SourcesEvent, StepEvent, TextDelta
from services.chat_service import ConversationNotFound, TurnOptions, TurnOutcome, run_turn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])


def _build_options(payload: ChatRequest, settings: Settings) -> TurnOptions:
    opts = payload.options
    if opts.models and not settings.allow_model_override:
        # Le choix du modèle est un levier de coût : jamais exposé au navigateur.
        raise forbidden("La surcharge de modèles est désactivée sur ce service.")
    if len(payload.question) > settings.max_question_chars:
        raise ApiError(422, "validation_error",
                       f"Question trop longue (max {settings.max_question_chars} caractères).")
    return TurnOptions(
        active_domains=opts.active_domains,
        use_justicelibre=True if opts.use_justicelibre is None else opts.use_justicelibre,
        use_fiscalonline=opts.use_fiscalonline,
        models_config=opts.models if settings.allow_model_override else None,
        auto_escalate=settings.followup_auto_escalate,
        deadline_s=settings.pipeline_deadline_s,
    )


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    principal: Principal = Depends(require_principal),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Traite une question et streame la progression puis la réponse (SSE)."""
    options = _build_options(payload, settings)
    get_rate_limiter(settings).check(principal.key)
    bind_conversation(payload.resolved_conversation_id)

    # La place est prise AVANT d'ouvrir le flux : à ce stade un 429 JSON propre
    # est encore possible, ce qui ne le serait plus une fois les en-têtes envoyés.
    slot = await PipelineSlot().__aenter__()
    encoder = SSEEncoder(settings.ai_sdk_protocol)
    outcome = TurnOutcome(conversation_id=payload.resolved_conversation_id or "",
                          message_id="")

    def make_iterator(cancel):
        return run_turn(
            payload.question,
            user_email=principal.email,
            conversation_id=payload.resolved_conversation_id,
            require_existing=payload.require_existing,
            options=options,
            cancel=cancel,
            outcome=outcome,
        )

    async def body() -> AsyncIterator[str]:
        text_open = False
        try:
            yield encoder.start(outcome.message_id or "m_pending")

            async for event in stream_events(make_iterator):
                if event is None:                       # keep-alive
                    yield encoder.heartbeat()
                elif isinstance(event, StepEvent):
                    yield encoder.progress(
                        step=event.step, label=event.label, status=event.status,
                        progress=event.progress, index=event.index, total=event.total,
                        elapsed_s=event.elapsed_s, meta=event.meta,
                    )
                elif isinstance(event, SourcesEvent):
                    yield encoder.sources(event.sources)
                elif isinstance(event, TextDelta):
                    if not text_open:
                        text_open = True
                        yield encoder.text_start()
                    yield encoder.text_delta(event.delta)
                elif isinstance(event, ResultEvent):     # consommé par run_turn
                    continue

            if text_open:
                yield encoder.text_end()
            if outcome.points_cles:
                yield encoder.points_cles(outcome.points_cles)
            if outcome.error:
                yield encoder.error("pipeline_failed", outcome.error)
            yield encoder.meta(
                conversation_id=outcome.conversation_id,
                message_id=outcome.message_id,
                trace_id=outcome.trace_id,
                is_follow_up=outcome.is_follow_up,
                escalated=outcome.escalated,
                cost_usd=round(outcome.cost_usd, 5),
                duration_s=outcome.duration_s,
                saved=outcome.saved,
                request_id=current_request_id(),
            )

        except ConversationNotFound:
            yield encoder.error("not_found", "Conversation introuvable.")
        except PipelineDeadlineExceeded:
            yield encoder.error("pipeline_timeout", "Le traitement a dépassé le temps imparti.")
        except Exception as exc:  # noqa: BLE001 — un flux ne doit jamais rester pendant
            logger.exception("Flux de chat en échec : %s", exc)
            yield encoder.error("internal_error", "Une erreur interne est survenue.")
        finally:
            if outcome.trace_id:
                bind_trace(outcome.trace_id)
            slot.release()
            yield encoder.finish()

    headers = sse_headers(settings.ai_sdk_protocol)
    if current_request_id():
        headers["x-request-id"] = current_request_id()
    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)


@router.post("/chat/sync", response_model=ChatSyncResponse)
async def chat_sync(
    payload: ChatRequest,
    principal: Principal = Depends(require_principal),
    settings: Settings = Depends(get_settings),
) -> ChatSyncResponse:
    """Même traitement, rendu en un seul JSON (pas de streaming)."""
    options = _build_options(payload, settings)
    get_rate_limiter(settings).check(principal.key)
    bind_conversation(payload.resolved_conversation_id)

    outcome = TurnOutcome(conversation_id=payload.resolved_conversation_id or "",
                          message_id="")

    def make_iterator(cancel):
        return run_turn(
            payload.question,
            user_email=principal.email,
            conversation_id=payload.resolved_conversation_id,
            require_existing=payload.require_existing,
            options=options,
            cancel=cancel,
            outcome=outcome,
        )

    async with PipelineSlot():
        try:
            async for _ in stream_events(make_iterator):
                pass
        except ConversationNotFound:
            raise not_found("Conversation introuvable.")
        except PipelineDeadlineExceeded:
            raise pipeline_timeout()

    if outcome.error and not outcome.answer:
        raise ApiError(502, "pipeline_failed", "Le pipeline n'a pas pu produire de réponse.",
                       retriable=True)

    bind_trace(outcome.trace_id)
    return ChatSyncResponse(
        conversation_id=outcome.conversation_id,
        message_id=outcome.message_id,
        answer=outcome.answer,
        points_cles=outcome.points_cles,
        sources=outcome.sources,
        is_follow_up=outcome.is_follow_up,
        trace_id=outcome.trace_id,
        cost_usd=round(outcome.cost_usd, 5),
        duration_s=outcome.duration_s,
    )
