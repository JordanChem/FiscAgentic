"""
Chemin « question de suivi » : réutilise le contexte de la conversation plutôt
que de rejouer les 11 étapes du pipeline.

L'agent de suivi rend un JSON court en un seul appel — on le bufferise donc au
lieu de le streamer. Cela permet de lire `necessite_nouvelle_recherche` **avant**
d'avoir émis quoi que ce soit au client : si l'agent estime que la question sort
du contexte, l'appelant peut enchaîner sur le pipeline complet sans avoir affiché
une réponse qu'il faudrait ensuite rétracter.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agents.suivi import agent_suivi
from pipeline.core import DEFAULT_MODELS, TraceOptions
from utils.api_keys import get_api_keys
from utils.json_utils import clean_json_codefence, lire_json_beton
from utils.llm import finalize_trace, llm_trace

logger = logging.getLogger(__name__)

# Nombre de tours conservés dans le contexte transmis à l'agent de suivi.
MAX_HISTORIQUE_TOURS = 6


@dataclass
class FollowUpResult:
    """Sortie du chemin de suivi."""
    question: str
    answer_text: str
    points_cles: List[str] = field(default_factory=list)
    necessite_nouvelle_recherche: bool = False
    trace_id: Optional[str] = None
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    wall_clock_s: float = 0.0
    error: Optional[str] = None


def run_follow_up(
    question: str,
    contexte: Dict,
    *,
    models_config: Optional[Dict[str, str]] = None,
    config_name: Optional[str] = None,
    trace: Optional[TraceOptions] = None,
) -> FollowUpResult:
    """Répond à une question de suivi à partir du contexte de conversation.

    Args:
        question: la question de suivi.
        contexte: `contexte_conversation` (question_initial, reponse_initial,
            sources, analyse, historique).
        models_config: override {agent: nom_logique}.
        trace: session_id / user_id Langfuse (même session que la question initiale).
    """
    models = {**DEFAULT_MODELS, **(models_config or {})}
    trace = trace or TraceOptions()
    _, google_key, _ = get_api_keys()
    if not google_key:
        raise RuntimeError("Clé API manquante : GOOGLE_API_KEY")

    t0 = time.time()
    with llm_trace(
        name="fisca-suivi", input=question,
        session_id=trace.session_id, user_id=trace.user_id,
        tags=trace.tags or ["follow-up"], config_name=config_name,
    ) as ctx:
        try:
            raw = agent_suivi(question, contexte, google_key, model_name=models["suivi"])
            parsed = lire_json_beton(raw)

            answer = (parsed.get("reponse_redigee") or parsed.get("reponse") or "").strip()
            if not answer:
                # Même cascade de repli que le rédactionnel : un parse raté ne doit
                # pas se traduire par une réponse vide côté utilisateur.
                answer = clean_json_codefence(raw or "").strip()
                logger.warning("Suivi — réponse illisible (raw=%d chars), repli texte brut",
                               len(raw or ""))

            result = FollowUpResult(
                question=question,
                answer_text=answer,
                points_cles=parsed.get("points_cles", []) or [],
                necessite_nouvelle_recherche=bool(parsed.get("necessite_nouvelle_recherche")),
                trace_id=ctx.trace_id,
                total_cost_usd=ctx.total_cost,
                total_input_tokens=ctx.total_input_tokens,
                total_output_tokens=ctx.total_output_tokens,
                wall_clock_s=time.time() - t0,
            )
            finalize_trace(
                output=result.answer_text,
                metadata={"necessite_nouvelle_recherche": result.necessite_nouvelle_recherche},
            )
            return result

        except Exception as exc:
            logger.exception("run_follow_up — échec sur : %r", question[:80])
            finalize_trace(metadata={"error": f"{type(exc).__name__}: {exc}"})
            return FollowUpResult(
                question=question, answer_text="", trace_id=ctx.trace_id,
                wall_clock_s=time.time() - t0,
                error=f"{type(exc).__name__}: {exc}",
            )


def build_contexte(
    question: str,
    answer: str,
    sources: List[Dict],
    analyse: Dict,
    previous: Optional[Dict] = None,
) -> Dict:
    """Construit / rafraîchit le `contexte_conversation` après un tour.

    Corrige un défaut de l'app historique : le contexte n'y était écrit qu'après
    la **première** question, si bien que tous les suivis d'une longue
    conversation raisonnaient indéfiniment sur le premier échange. Ici la
    question et la réponse initiales sont conservées (elles ancrent le sujet),
    mais l'historique récent est ajouté et les sources sont rafraîchies.
    """
    historique = list((previous or {}).get("historique", []))
    historique.append({"question": question, "reponse": (answer or "")[:2000]})

    return {
        "question_initial": (previous or {}).get("question_initial", question),
        "reponse_initial": (previous or {}).get("reponse_initial", answer),
        "sources": sources,
        "analyse": analyse,
        "historique": historique[-MAX_HISTORIQUE_TOURS:],
    }
