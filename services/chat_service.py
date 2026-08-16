"""
Orchestration d'un tour de conversation.

Sans import FastAPI : ce module reste testable seul et réutilisable par l'UI de
debug. Il produit les mêmes événements que `pipeline.core` (plus quelques-uns qui
lui sont propres) ; c'est la route HTTP qui les traduit en SSE.

Décision de routage :

    conversation_id absent / conversation sans contexte  → pipeline complet
    contexte présent                                     → agent de suivi
        └─ necessite_nouvelle_recherche → enchaîne sur le pipeline complet

Le `contexte_conversation` ne quitte jamais le serveur : le front n'envoie qu'un
`conversation_id`.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from pipeline.core import PipelineResult, TraceOptions, run_pipeline_stream
from pipeline.events import (
    AUX_STEP_LABELS, PipelineEvent, ResultEvent, SourcesEvent, StepEvent, TextDelta,
    public_sources,
)
from pipeline.followup import FollowUpResult, build_contexte, run_follow_up
from utils.conversations import load_conversation, save_conversation

logger = logging.getLogger(__name__)

# Taille des fragments quand on « rejoue » un texte déjà complet (réponse de
# suivi) pour que le rendu reste comparable à un vrai streaming.
_REPLAY_CHUNK = 60


class ConversationNotFound(Exception):
    """Conversation inexistante — ou appartenant à un autre utilisateur."""


# Espace de noms pour dériver un UUID stable d'un identifiant client.
_CONVERSATION_NS = uuid.UUID("6f9b1c2e-0a4d-4f8b-9c3e-5d7a1b2c3d4e")


def normalize_conversation_id(raw: Optional[str], user_email: str) -> str:
    """Normalise un identifiant de conversation en UUID.

    La colonne `conversations.id` est de type `uuid` côté Postgres, alors que
    `useChat` génère des identifiants alphanumériques quelconques et les envoie
    dès le premier message. Les stocker tels quels échoue — silencieusement, du
    point de vue de l'utilisateur : la réponse s'affiche mais rien n'est
    persisté.

    On dérive donc un UUID **déterministe** (uuid5) de l'identifiant client :
    un même chat retombe toujours sur la même ligne, sans imposer de changement
    ni au front ni au schéma. La dérivation inclut l'email pour que deux
    utilisateurs ayant tiré le même identifiant côté client ne se télescopent pas.
    """
    if not raw:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(_CONVERSATION_NS, f"{user_email}:{raw}"))


@dataclass
class TurnOptions:
    active_domains: Optional[List[str]] = None
    use_justicelibre: bool = True
    use_fiscalonline: Optional[bool] = None
    models_config: Optional[Dict[str, str]] = None
    auto_escalate: bool = True
    deadline_s: Optional[float] = None


@dataclass
class TurnOutcome:
    """Bilan d'un tour, disponible après épuisement du générateur."""
    conversation_id: str
    message_id: str
    answer: str = ""
    points_cles: List[str] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    is_follow_up: bool = False
    escalated: bool = False
    trace_id: Optional[str] = None
    cost_usd: float = 0.0
    duration_s: float = 0.0
    error: Optional[str] = None
    saved: bool = False


def run_turn(
    question: str,
    *,
    user_email: str,
    conversation_id: Optional[str] = None,
    require_existing: bool = False,
    options: Optional[TurnOptions] = None,
    cancel: Optional[threading.Event] = None,
    outcome: Optional[TurnOutcome] = None,
) -> Iterator[PipelineEvent]:
    """Traite un tour de conversation et émet les événements correspondants.

    Args:
        conversation_id: conversation à poursuivre, ou identifiant à adopter
            pour une nouvelle conversation (cas de `useChat`, qui génère son id
            côté client et l'envoie dès le premier message).
        require_existing: True quand l'appelant affirme que la conversation
            existe (champ `conversation_id` explicite) → 404 si absente.
        outcome: objet renseigné au fil de l'eau (l'appelant le lit après le
            flux, y compris si le client s'est déconnecté).

    Raises:
        ConversationNotFound: `require_existing` et conversation introuvable
            pour cet utilisateur.
    """
    options = options or TurnOptions()
    t0 = time.time()

    # L'identifiant doit exister AVANT l'ouverture de la trace : il sert de
    # session_id Langfuse et regroupe tous les tours d'une même conversation.
    raw_conversation_id = conversation_id
    conversation_id = normalize_conversation_id(conversation_id, user_email)

    existing = None
    if raw_conversation_id:                # sinon : conversation neuve, rien à charger
        existing = load_conversation(conversation_id, user_email=user_email)
        if existing is None and require_existing:
            raise ConversationNotFound(raw_conversation_id)

    messages: List[Dict[str, Any]] = list((existing or {}).get("messages") or [])
    contexte: Optional[Dict] = (existing or {}).get("contexte_conversation") or None
    outcome = outcome if outcome is not None else TurnOutcome(conversation_id, "")
    outcome.conversation_id = conversation_id
    outcome.message_id = f"m_{uuid.uuid4().hex[:16]}"

    trace = TraceOptions(
        session_id=conversation_id,
        user_id=user_email,
        tags=["follow-up"] if contexte else ["question"],
    )

    answer = ""
    points_cles: List[str] = []
    sources: List[Dict[str, Any]] = []
    analyse: Dict = (contexte or {}).get("analyse", {})
    run_full = True

    try:
        # ── Chemin de suivi ──────────────────────────────────────────────────
        if contexte:
            yield StepEvent(step="suivi", label=AUX_STEP_LABELS["suivi"], status="running")
            follow: FollowUpResult = run_follow_up(
                question, contexte,
                models_config=options.models_config, trace=trace,
            )
            yield StepEvent(step="suivi", label=AUX_STEP_LABELS["suivi"], status="done",
                            elapsed_s=round(follow.wall_clock_s, 2))

            escalate = follow.necessite_nouvelle_recherche and options.auto_escalate
            if follow.error:
                logger.warning("Suivi en échec, bascule sur le pipeline complet : %s",
                               follow.error)
                escalate = True

            if not escalate:
                # Rien n'a encore été streamé : on peut rendre la réponse du suivi.
                run_full = False
                answer = follow.answer_text
                points_cles = follow.points_cles
                sources = contexte.get("sources", []) or []
                outcome.is_follow_up = True
                outcome.trace_id = follow.trace_id
                outcome.cost_usd = follow.total_cost_usd

                yield SourcesEvent(sources=public_sources(sources))
                for fragment in _replay(answer):
                    if cancel is not None and cancel.is_set():
                        break
                    yield TextDelta(fragment)
            else:
                outcome.escalated = True
                yield StepEvent(step="escalade", label=AUX_STEP_LABELS["escalade"],
                                status="running",
                                meta={"raison": "necessite_nouvelle_recherche"})

        # ── Pipeline complet ─────────────────────────────────────────────────
        if run_full:
            result: Optional[PipelineResult] = None
            for event in run_pipeline_stream(
                question,
                models_config=options.models_config,
                active_domains=options.active_domains,
                use_justicelibre=options.use_justicelibre,
                use_fiscalonline=options.use_fiscalonline,
                stream_redaction=True,
                trace=trace,
                cancel=cancel,
                deadline_s=options.deadline_s,
            ):
                if isinstance(event, ResultEvent):
                    result = event.result
                    continue
                yield event

            if result is not None:
                answer = result.answer_text
                points_cles = result.points_cles
                sources = result.sources
                analyse = result.analyste
                outcome.trace_id = result.trace_id
                outcome.cost_usd = result.total_cost_usd
                outcome.error = result.error

        outcome.answer = answer
        outcome.points_cles = points_cles
        outcome.sources = sources
        outcome.duration_s = round(time.time() - t0, 2)

    finally:
        # Persiste ce qui a été produit, y compris sur déconnexion : une réponse
        # à moitié streamée reste utile à l'utilisateur au rechargement.
        outcome.duration_s = outcome.duration_s or round(time.time() - t0, 2)
        if answer:
            outcome.saved = _persist(
                conversation_id=conversation_id,
                user_email=user_email,
                messages=messages,
                question=question,
                answer=answer,
                points_cles=points_cles,
                sources=sources,
                analyse=analyse,
                previous_contexte=contexte,
                message_id=outcome.message_id,
                trace_id=outcome.trace_id,
                is_follow_up=outcome.is_follow_up,
            )


# ─── Persistance ──────────────────────────────────────────────────────────────
def _persist(*, conversation_id: str, user_email: str, messages: List[Dict],
             question: str, answer: str, points_cles: List[str],
             sources: List[Dict], analyse: Dict, previous_contexte: Optional[Dict],
             message_id: str, trace_id: Optional[str], is_follow_up: bool) -> bool:
    messages = messages + [
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "content": answer,
            "id": message_id,
            "points_cles": points_cles,
            "sources": sources,
            "trace_id": trace_id,
            "is_follow_up": is_follow_up,
        },
    ]
    contexte = build_contexte(question, answer, sources, analyse, previous_contexte)
    try:
        return save_conversation(conversation_id, messages, contexte, user_email=user_email)
    except Exception as exc:  # pragma: no cover — la persistance ne doit pas casser le tour
        logger.error("Sauvegarde de la conversation %s échouée : %s", conversation_id, exc)
        return False


def _replay(text: str) -> Iterator[str]:
    """Découpe un texte déjà complet en fragments, pour un rendu progressif."""
    for i in range(0, len(text), _REPLAY_CHUNK):
        yield text[i:i + _REPLAY_CHUNK]
