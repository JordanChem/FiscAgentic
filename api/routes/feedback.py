"""Feedback utilisateur : ligne Supabase + score sur la trace Langfuse."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import Principal, require_principal
from api.schemas import FeedbackRequest, OkResponse
from utils.feedback import save_feedback

router = APIRouter(prefix="/v1", tags=["feedback"])


@router.post("/feedback", response_model=OkResponse)
def create_feedback(
    payload: FeedbackRequest,
    principal: Principal = Depends(require_principal),
) -> OkResponse:
    """Enregistre un 👍/👎.

    `trace_id` (renvoyé dans `data-meta` du flux de chat) attache la note à la
    trace Langfuse correspondante : les réponses mal notées deviennent
    directement filtrables dans le dashboard.
    """
    ok = save_feedback(
        question=payload.question,
        answer=payload.answer,
        rating=payload.rating,
        comment=payload.comment,
        sources_count=payload.sources_count,
        is_follow_up=payload.is_follow_up,
        user_email=principal.email,
        trace_id=payload.trace_id,
    )
    return OkResponse(ok=ok)
