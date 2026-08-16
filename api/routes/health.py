"""Sondes de disponibilité."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import Principal, require_principal
from api.runner import free_slots
from api.schemas import HealthResponse, ReadyResponse
from api.settings import Settings, get_settings
from services.supabase import is_supabase_ready

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Liveness : aucune I/O, répond tant que le process est vivant."""
    return HealthResponse(status="ok", app=settings.app_name, environment=settings.environment)


@router.get("/ready", response_model=ReadyResponse)
def ready(
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(require_principal),
) -> ReadyResponse:
    """Readiness : secrets présents et Supabase joignable.

    Endpoint `def` (et non `async`) : la sonde Supabase est bloquante, elle doit
    tourner dans le threadpool pour ne pas figer les flux SSE en cours.
    """
    missing = settings.missing_required()
    supabase_ok = is_supabase_ready()
    status = "ok" if supabase_ok and not missing else "degraded"
    return ReadyResponse(
        status=status,
        supabase=supabase_ok,
        missing_config=missing,
        free_pipeline_slots=free_slots(),
    )
