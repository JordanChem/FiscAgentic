"""
Récupération centralisée des clés API (env d'abord, secrets Streamlit en repli).

Utilisable hors Streamlit (pipeline d'éval, CLI) : si `streamlit` n'est pas
disponible ou qu'aucun contexte de script n'existe, on se rabat silencieusement
sur les variables d'environnement (chargées via python-dotenv).
"""
import os
from typing import Tuple


def _get(key: str) -> str:
    val = os.getenv(key)
    if val:
        return val
    try:
        import streamlit as st
        return st.secrets.get(key, "")  # type: ignore[attr-defined]
    except Exception:
        return ""


def get_api_keys() -> Tuple[str, str, str]:
    """Retourne (openai_key, google_key, serpapi_key)."""
    return _get("OPENAI_API_KEY"), _get("GOOGLE_API_KEY"), _get("SERPAPI_API_KEY")


def get_anthropic_key() -> str:
    return _get("ANTHROPIC_API_KEY")
