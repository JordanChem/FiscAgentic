"""
Schémas Pydantic de l'API.

`ChatRequest` accepte deux formes de corps :
  * `{"message": "…"}` — appelant serveur simple (le proxy fiscalonline) ;
  * le corps natif du hook `useChat` de l'AI SDK, qui poste
    `{"id": …, "messages": [{"role": "user", "parts": [{"type": "text", "text": …}]}]}`.

Tolérer les deux évite d'imposer une transformation au développeur front tout en
gardant une API utilisable en `curl`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from utils.search import OFFICIAL_DOMAINS


class ChatOptions(BaseModel):
    active_domains: Optional[List[str]] = None
    use_justicelibre: Optional[bool] = None
    use_fiscalonline: Optional[bool] = None
    # Réservé au debug interne : ignoré (et rejeté) si ALLOW_MODEL_OVERRIDE=false.
    models: Optional[Dict[str, str]] = None

    @model_validator(mode="after")
    def _check_domains(self):
        if self.active_domains is not None:
            unknown = [d for d in self.active_domains if d not in OFFICIAL_DOMAINS]
            if unknown:
                raise ValueError(
                    f"Domaines inconnus : {', '.join(unknown)}. "
                    f"Autorisés : {', '.join(OFFICIAL_DOMAINS)}"
                )
        return self


class ChatRequest(BaseModel):
    """Corps de `/v1/chat`.

    Deux identifiants aux sémantiques **différentes** :

    * `conversation_id` — référence à une conversation que l'appelant affirme
      exister : si elle est introuvable, c'est une erreur (404).
    * `id` — identifiant de chat généré **côté client** par `useChat`. Il est
      envoyé dès le tout premier message, alors que rien n'existe encore côté
      serveur : il faut donc l'adopter et créer la conversation sous cet id.
    """

    id: Optional[str] = None                    # id de chat envoyé par useChat
    conversation_id: Optional[str] = None       # référence à une conversation existante
    message: Optional[str] = None               # appelant serveur simple
    messages: Optional[List[Dict[str, Any]]] = None   # UIMessage[] de l'AI SDK
    options: ChatOptions = Field(default_factory=ChatOptions)

    # Remplis par le validateur.
    question: str = ""
    resolved_conversation_id: Optional[str] = None
    require_existing: bool = False

    @model_validator(mode="after")
    def _resolve(self):
        question = (self.message or "").strip()
        if not question and self.messages:
            question = _last_user_text(self.messages)
        if not question:
            raise ValueError("Aucune question fournie (`message` ou `messages` requis).")
        self.question = question
        self.resolved_conversation_id = self.conversation_id or self.id
        self.require_existing = bool(self.conversation_id)
        return self


def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    """Extrait le texte du dernier message utilisateur (formats AI SDK v4 et v5)."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        parts = msg.get("parts")
        if isinstance(parts, list):             # v5 : UIMessage.parts
            texts = [p.get("text", "") for p in parts
                     if isinstance(p, dict) and p.get("type") == "text"]
            joined = "".join(texts).strip()
            if joined:
                return joined
        content = msg.get("content")            # v4 : content simple
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content
                     if isinstance(c, dict) and c.get("type") == "text"]
            joined = "".join(texts).strip()
            if joined:
                return joined
    return ""


# ── Réponses ─────────────────────────────────────────────────────────────────
class SourceOut(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    snippet: Optional[str] = None
    source_domain: Optional[str] = None
    score: Optional[float] = None
    provider: Optional[str] = None


class ChatSyncResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    points_cles: List[str] = Field(default_factory=list)
    sources: List[SourceOut] = Field(default_factory=list)
    is_follow_up: bool = False
    trace_id: Optional[str] = None
    cost_usd: float = 0.0
    duration_s: float = 0.0


class ConversationSummary(BaseModel):
    id: str
    title: Optional[str] = None
    message_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ConversationDetail(BaseModel):
    id: str
    title: Optional[str] = None
    message_count: int = 0
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class FeedbackRequest(BaseModel):
    conversation_id: Optional[str] = None
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(default="", max_length=100_000)
    rating: int = Field(ge=0, le=1, description="0 = 👎, 1 = 👍")
    comment: Optional[str] = Field(default=None, max_length=4000)
    sources_count: int = 0
    is_follow_up: bool = False
    trace_id: Optional[str] = None


class OkResponse(BaseModel):
    ok: bool = True


class DomainOut(BaseModel):
    domain: str
    label: str


class ConfigResponse(BaseModel):
    domains: List[DomainOut]
    default_active_domains: List[str]
    steps: List[Dict[str, Any]]
    max_question_chars: int
    ai_sdk_protocol: str


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str


class ReadyResponse(BaseModel):
    status: str
    supabase: bool
    missing_config: List[str] = Field(default_factory=list)
    free_pipeline_slots: int = 0
