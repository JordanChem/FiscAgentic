"""
Dépendances FastAPI : identité, quotas, capacité.

Modèle de confiance : l'API de fiscalonline authentifie l'abonné de son côté,
puis relaie l'appel avec un secret partagé et l'identité de l'utilisateur. Ce
service ne fait donc **aucune** authentification — mais il ne doit jamais
accepter une identité venant d'ailleurs que des en-têtes signés par ce secret.

Corollaire de sécurité : toutes les requêtes Supabase sont filtrées par
`principal.user_email`, jamais par une valeur du corps de requête. L'isolation
entre utilisateurs est purement applicative (cf. `utils/conversations.py`), donc
un filtre oublié exposerait les conversations de tous les abonnés.
"""
from __future__ import annotations

import hmac
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict, Optional

from fastapi import Header

from api.errors import rate_limited, unauthorized
from api.settings import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    """Utilisateur relayé par le proxy fiscalonline."""
    email: str
    user_id: Optional[str] = None

    @property
    def key(self) -> str:
        return self.user_id or self.email


def require_principal(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    x_user_email: Optional[str] = Header(default=None, alias="X-User-Email"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> Principal:
    settings = get_settings()
    secrets = settings.shared_secrets
    if not secrets:
        raise unauthorized("Le service n'est pas configuré (API_SHARED_SECRET absent).")
    if not x_api_key:
        raise unauthorized("En-tête X-API-Key manquant.")
    # compare_digest : comparaison à temps constant, `==` fuirait le secret.
    if not any(hmac.compare_digest(x_api_key, s) for s in secrets):
        raise unauthorized()
    if not x_user_email:
        raise unauthorized("En-tête X-User-Email manquant.")

    return Principal(email=x_user_email.strip().lower(), user_id=x_user_id)


# ─── Limitation de débit (en mémoire, mono-process) ───────────────────────────
class RateLimiter:
    """Fenêtre glissante par utilisateur : quota horaire + garde-fou par minute.

    Volontairement en mémoire : un seul serveur, quelques workers. Passer à Redis
    seulement le jour où le service est répliqué (sinon le quota est par worker).
    """

    def __init__(self, per_hour: int, burst_per_min: int):
        self.per_hour = per_hour
        self.burst_per_min = burst_per_min
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > 3600:
                hits.popleft()
            if len(hits) >= self.per_hour:
                raise rate_limited(retry_after=int(3600 - (now - hits[0])) + 1)
            recent = sum(1 for t in hits if now - t < 60)
            if recent >= self.burst_per_min:
                raise rate_limited(retry_after=60)
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter: Optional[RateLimiter] = None


def get_rate_limiter(settings: Optional[Settings] = None) -> RateLimiter:
    global _limiter
    if _limiter is None:
        settings = settings or get_settings()
        _limiter = RateLimiter(settings.rate_limit_per_hour, settings.rate_limit_burst_per_min)
    return _limiter


def reset_rate_limiter() -> None:
    """Tests uniquement."""
    global _limiter
    _limiter = None
