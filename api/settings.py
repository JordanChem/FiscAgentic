"""
Configuration du service — source unique des variables d'environnement.

Tout est surchargeable par variable d'env (ou par `.env`). Les valeurs par
défaut correspondent au déploiement cible : un process derrière le
reverse-proxy de fiscalonline, sur le serveur du client.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False,
    )

    # ── Identité du service ──────────────────────────────────────────────────
    app_name: str = "fisca-api"
    environment: Literal["dev", "staging", "prod"] = "dev"

    # ── Authentification du proxy fiscalonline ───────────────────────────────
    # Liste séparée par des virgules → permet la rotation de clé sans coupure.
    api_shared_secrets: str = Field(default="", alias="API_SHARED_SECRET")

    # ── Réseau ───────────────────────────────────────────────────────────────
    # Loopback par défaut : le reverse-proxy du client est la seule entrée.
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    # CORS désactivé par défaut (appels serveur-à-serveur). N'ouvrir que si le
    # navigateur appelle directement — et jamais avec "*" tant qu'un secret
    # partagé transite en en-tête.
    cors_origins: str = ""

    # ── Exécution du pipeline ────────────────────────────────────────────────
    max_concurrent_pipelines: int = 3
    slot_acquire_timeout_s: float = 2.0
    # Un run complet mesuré en recette tourne autour de 200-300 s (11 étapes,
    # ~20 appels LLM, recherche et scraping). 600 s laisse la marge nécessaire
    # sans jamais laisser une requête traîner indéfiniment.
    pipeline_deadline_s: float = 600.0
    sse_heartbeat_s: float = 15.0

    # ── Comportement métier ──────────────────────────────────────────────────
    followup_auto_escalate: bool = True
    allow_model_override: bool = False
    ai_sdk_protocol: Literal["v5", "v4"] = "v5"

    # ── Limites ──────────────────────────────────────────────────────────────
    max_question_chars: int = 4000
    max_body_bytes: int = 64 * 1024
    rate_limit_per_hour: int = 30
    rate_limit_burst_per_min: int = 3

    # ── Observabilité ────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    @field_validator("api_shared_secrets", "cors_origins", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @property
    def shared_secrets(self) -> List[str]:
        return [s.strip() for s in self.api_shared_secrets.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def missing_required(self) -> List[str]:
        """Secrets indispensables au démarrage — l'absence fait échouer vite."""
        from utils.api_keys import get_api_keys

        missing: List[str] = []
        if not self.shared_secrets:
            missing.append("API_SHARED_SECRET")
        openai_key, google_key, serpapi_key = get_api_keys()
        for name, value in (("OPENAI_API_KEY", openai_key),
                            ("GOOGLE_API_KEY", google_key),
                            ("SERPAPI_API_KEY", serpapi_key)):
            if not value:
                missing.append(name)
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Vide le cache (tests)."""
    get_settings.cache_clear()
