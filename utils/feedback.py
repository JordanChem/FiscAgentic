"""
Module de feedback utilisateur avec stockage Supabase
"""
import os
import streamlit as st
from supabase import create_client


def get_supabase_client():
    """Initialise et retourne le client Supabase"""
    url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    return create_client(url, key)


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
        st.error(f"Erreur lors de l'envoi du feedback : {e}")
        return False
