"""
Client Supabase partagé (singleton).

Avant, `get_supabase_client()` appelait `create_client()` à chaque invocation :
un nouveau client httpx — donc un nouveau pool TCP — était construit 4 à 6 fois
par requête. Ici le client est mis en cache pour la durée du process.

`reset_supabase_client()` existe pour les tests (et pour recharger après une
rotation de secrets sans redémarrer).
"""
import logging
from functools import lru_cache
from typing import Optional

from supabase import Client, create_client

from utils.api_keys import get_supabase_config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase_client() -> Optional[Client]:
    """Retourne le client Supabase partagé, ou None si non configuré."""
    url, key = get_supabase_config()
    if not url or not key:
        logger.warning("Supabase non configuré (SUPABASE_URL / SUPABASE_KEY manquants).")
        return None
    try:
        return create_client(url, key)
    except Exception as exc:
        logger.error("Supabase — création du client échouée : %s", exc)
        return None


def reset_supabase_client() -> None:
    """Vide le cache du client (tests, rotation de secrets)."""
    get_supabase_client.cache_clear()


def is_supabase_ready() -> bool:
    """Sonde légère pour le endpoint /ready : le client existe et répond."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table("conversations").select("id").limit(1).execute()
        return True
    except Exception as exc:
        logger.warning("Supabase — sonde /ready en échec : %s", exc)
        return False
