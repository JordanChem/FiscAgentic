"""
Historique des conversations.

Endpoints `def` (threadpool) : le client Supabase est bloquant.

Toutes les requêtes sont filtrées par `principal.email`. Ne jamais accepter un
`user_email` venant du corps ou de la query : l'isolation entre abonnés ne tient
qu'à ce filtre applicatif.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query

from api.deps import Principal, require_principal
from api.errors import not_found
from api.schemas import ConversationDetail, ConversationSummary, OkResponse
from services.chat_service import normalize_conversation_id
from utils.conversations import delete_conversation, list_conversations, load_conversation

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("", response_model=List[ConversationSummary])
def list_(
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_principal),
) -> List[ConversationSummary]:
    rows = list_conversations(limit=limit, user_email=principal.email)
    return [ConversationSummary(**{k: v for k, v in row.items()
                                   if k in ConversationSummary.model_fields})
            for row in rows]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get(
    conversation_id: str,
    principal: Principal = Depends(require_principal),
) -> ConversationDetail:
    row = load_conversation(normalize_conversation_id(conversation_id, principal.email),
                            user_email=principal.email)
    if not row:
        # Même réponse qu'une conversation appartenant à autrui : ne pas révéler
        # l'existence d'un identifiant.
        raise not_found("Conversation introuvable.")
    return ConversationDetail(
        id=row.get("id", conversation_id),
        title=row.get("title"),
        message_count=row.get("message_count", 0),
        messages=row.get("messages") or [],
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.delete("/{conversation_id}", response_model=OkResponse)
def delete(
    conversation_id: str,
    principal: Principal = Depends(require_principal),
) -> OkResponse:
    if not load_conversation(conversation_id, user_email=principal.email):
        raise not_found("Conversation introuvable.")
    return OkResponse(ok=delete_conversation(conversation_id, user_email=principal.email))
