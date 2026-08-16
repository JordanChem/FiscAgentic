"""
Récupération centralisée des secrets (env d'abord, secrets Streamlit en repli).

Utilisable hors Streamlit (API FastAPI, pipeline d'éval, CLI) : si `streamlit`
n'est pas disponible ou qu'aucun contexte de script n'existe, on se rabat
silencieusement sur les variables d'environnement (chargées via python-dotenv).

C'est le SEUL module du projet autorisé à importer `streamlit`, et uniquement
dans un try/except : tout le reste (`utils/`, `pipeline/`, `agents/`, `api/`)
doit pouvoir s'importer dans un environnement sans Streamlit installé.
"""
import os
from typing import Tuple


def get_secret(key: str, default: str = "") -> str:
    """Lit un secret : variable d'environnement, puis `st.secrets`, puis `default`."""
    val = os.getenv(key)
    if val:
        return val
    try:
        import streamlit as st
        return st.secrets.get(key, default)  # type: ignore[attr-defined]
    except Exception:
        return default


# Alias historique (conservé : utilisé par les modules déjà écrits).
_get = get_secret


def get_api_keys() -> Tuple[str, str, str]:
    """Retourne (openai_key, google_key, serpapi_key)."""
    return get_secret("OPENAI_API_KEY"), get_secret("GOOGLE_API_KEY"), get_secret("SERPAPI_API_KEY")


def get_anthropic_key() -> str:
    return get_secret("ANTHROPIC_API_KEY")


def get_supabase_config() -> Tuple[str, str]:
    """Retourne (supabase_url, supabase_key). Chaîne vide si non configuré."""
    return get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY")


def get_fiscalonline_token() -> str:
    """Retourne le token de l'API interne FiscalOnline (chaîne vide si absent)."""
    return get_secret("FISCALONLINE_TOKEN")
