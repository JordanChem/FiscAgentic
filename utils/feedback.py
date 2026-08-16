"""
Module de feedback utilisateur avec stockage Supabase.

Sans dépendance Streamlit : utilisable depuis l'API FastAPI comme depuis l'UI
de debug. Les erreurs sont journalisées et remontées via la valeur de retour
(`bool`), c'est à l'appelant de décider comment les présenter.
"""
import logging

from services.supabase import get_supabase_client  # noqa: F401  (ré-export historique)

logger = logging.getLogger(__name__)


def _attach_langfuse_score(trace_id: str, rating: int, comment: str = None) -> None:
    """Attache le 👍/👎 comme score sur la trace Langfuse correspondante (best-effort).

    Permet de filtrer/retrouver dans Langfuse les traces mal notées → boucle directe
    « qualité des sorties ». No-op si trace_id absent ou Langfuse non configuré.
    """
    if not trace_id:
        return
    try:
        from utils.llm import _get_langfuse
        lf = _get_langfuse()
        if lf is None:
            return
        lf.score(trace_id=trace_id, name="user_feedback", value=rating, comment=comment)
        lf.flush()
    except Exception:  # pragma: no cover — l'observabilité ne doit pas casser le feedback
        pass


def save_feedback(question: str, answer: str, rating: int, comment: str = None,
                  sources_count: int = 0, is_follow_up: bool = False,
                  user_email: str = None, trace_id: str = None) -> bool:
    """
    Enregistre un feedback dans Supabase.

    Args:
        question: La question posée par l'utilisateur
        answer: La réponse de l'assistant (tronquée à 5000 chars)
        rating: 0 = pouce bas, 1 = pouce haut
        comment: Commentaire optionnel
        sources_count: Nombre de sources citées
        is_follow_up: Question de suivi ou nouvelle question
        user_email: Email de l'utilisateur connecté
        trace_id: Id de la trace Langfuse à noter (optionnel)

    Returns:
        True si le feedback a été enregistré, False sinon
    """
    # Score Langfuse indépendant de Supabase (utile même si Supabase indisponible).
    _attach_langfuse_score(trace_id, rating, comment)

    client = get_supabase_client()
    if not client:
        return False

    try:
        data = {
            "question": question,
            "answer": answer[:5000],
            "rating": rating,
            "comment": comment,
            "sources_count": sources_count,
            "is_follow_up": is_follow_up,
        }
        if user_email:
            data["user_email"] = user_email
        client.table("feedbacks").insert(data).execute()
        return True
    except Exception as e:
        logger.error("Erreur lors de l'envoi du feedback : %s", e)
        return False
