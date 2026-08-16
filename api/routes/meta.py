"""Configuration exposée au front (libellés de domaines, étapes, limites)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import Principal, require_principal
from api.schemas import ConfigResponse, DomainOut
from api.settings import Settings, get_settings
from pipeline.events import STEPS
from utils.search import OFFICIAL_DOMAINS

router = APIRouter(prefix="/v1", tags=["meta"])

# Libellés repris de l'UI Streamlit pour que le front affiche les mêmes noms.
DOMAIN_LABELS = {
    "legifrance.gouv.fr": "Légifrance",
    "bofip.impots.gouv.fr": "BOFiP",
    "conseil-etat.fr": "Conseil d'État",
    "courdecassation.fr": "Cour de cassation",
    "conseil-constitutionnel.fr": "Conseil constitutionnel",
    "assemblee-nationale.fr": "Assemblée nationale",
    "senat.fr": "Sénat",
    "fiscalonline.fr": "FiscalOnline",
    "europa.eu": "CJUE (europa.eu)",
    "opendata.justice-administrative.fr": "Cours administratives d'appel / TA",
}


@router.get("/config", response_model=ConfigResponse)
async def config(
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(require_principal),
) -> ConfigResponse:
    return ConfigResponse(
        domains=[DomainOut(domain=d, label=DOMAIN_LABELS.get(d, d)) for d in OFFICIAL_DOMAINS],
        default_active_domains=list(OFFICIAL_DOMAINS),
        steps=[{"step": sid, "label": label, "progress": pct} for sid, label, pct in STEPS],
        max_question_chars=settings.max_question_chars,
        ai_sdk_protocol=settings.ai_sdk_protocol,
    )
