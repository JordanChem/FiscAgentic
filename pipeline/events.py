"""
Événements émis par `pipeline.core.run_pipeline_stream`.

Ce module est le contrat entre le pipeline et ses consommateurs (API SSE, UI
Streamlit de debug, CLI `test_pipeline.py`). Il ne dépend d'aucun framework :
c'est ce qui permet au même générateur d'alimenter une `StreamingResponse`
FastAPI et un `st.write_stream`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

StepStatus = Literal["running", "done", "skipped", "error"]


# ─── Étapes du pipeline ───────────────────────────────────────────────────────
# (identifiant, libellé utilisateur, avancement en % une fois l'étape terminée)
# Les libellés viennent de l'UI Streamlit historique afin de ne pas changer ce
# que voit l'utilisateur final.
STEPS: List[tuple[str, str, int]] = [
    ("analyse",        "Analyse de la question",                     10),
    ("routage",        "Routage vers les agents spécialisés",        20),
    ("specialistes",   "Consultation des agents spécialisés",        30),
    ("verification",   "Vérification et nettoyage des sources",      40),
    ("requetes",       "Génération des requêtes de recherche",       45),
    ("jurisprudence",  "Recherche de jurisprudence",                 50),
    ("recherche",      "Recherche des sources officielles",          60),
    ("deduplication",  "Déduplication des résultats",                65),
    ("ranking",        "Classement des sources",                     70),
    ("scraping",       "Extraction du contenu des sources",          80),
    ("redaction",      "Rédaction de la réponse",                    90),
]

STEP_INDEX: Dict[str, int] = {sid: i + 1 for i, (sid, _, _) in enumerate(STEPS)}
STEP_LABEL: Dict[str, str] = {sid: label for sid, label, _ in STEPS}
STEP_PROGRESS: Dict[str, int] = {sid: pct for sid, _, pct in STEPS}
TOTAL_STEPS: int = len(STEPS)

# Étapes hors séquence principale (parallèles ou conditionnelles).
AUX_STEP_LABELS: Dict[str, str] = {
    "fiscalonline": "Récupération des articles FiscalOnline",
    "suivi":        "Analyse de votre question de suivi",
    "escalade":     "Nouvelle recherche complète nécessaire",
}


# ─── Événements ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StepEvent:
    """Avancement d'une étape. Émis en `running` puis en `done`/`skipped`/`error`."""
    step: str
    label: str
    status: StepStatus = "running"
    index: int = 0                 # 0 pour les étapes auxiliaires (hors séquence)
    total: int = TOTAL_STEPS
    progress: int = 0              # 0-100
    elapsed_s: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcesEvent:
    """Sources retenues, émises juste avant la rédaction (jamais le champ `content`)."""
    sources: List[Dict[str, Any]]


@dataclass(frozen=True)
class TextDelta:
    """Fragment de la réponse rédigée. Contenu **brut** du modèle : c'est au
    consommateur d'appliquer `pipeline.normalizer.RedactionNormalizer`."""
    delta: str


@dataclass(frozen=True)
class ResultEvent:
    """Dernier événement d'un flux réussi ou en erreur : le `PipelineResult` complet."""
    result: Any                    # PipelineResult (évite un import circulaire)


PipelineEvent = StepEvent | SourcesEvent | TextDelta | ResultEvent


# ─── Fabriques ────────────────────────────────────────────────────────────────
# Les métadonnées d'étape sont libres, à trois noms réservés près (`step`,
# `elapsed_s`, `status`) qui sont des paramètres de ces fabriques. Les
# consommateurs, eux, ne doivent jamais les splater dans une autre signature :
# une métadonnée `total` (nombre d'URL scrapées) a déjà fait échouer tout un
# flux SSE en heurtant le `total` de l'encodeur — voir `api/sse.py::progress`.
def step_started(step: str, **meta) -> StepEvent:
    return StepEvent(
        step=step,
        label=STEP_LABEL.get(step) or AUX_STEP_LABELS.get(step, step),
        status="running",
        index=STEP_INDEX.get(step, 0),
        progress=_progress_before(step),
        meta=meta,
    )


def step_finished(step: str, elapsed_s: float, status: StepStatus = "done", **meta) -> StepEvent:
    return StepEvent(
        step=step,
        label=STEP_LABEL.get(step) or AUX_STEP_LABELS.get(step, step),
        status=status,
        index=STEP_INDEX.get(step, 0),
        progress=STEP_PROGRESS.get(step, _progress_before(step)),
        elapsed_s=round(elapsed_s, 2),
        meta=meta,
    )


def _progress_before(step: str) -> int:
    """Avancement affiché pendant l'exécution d'une étape (= fin de la précédente)."""
    idx = STEP_INDEX.get(step)
    if idx is None:          # étape auxiliaire : on ne fait pas bouger la barre
        return 0
    return STEPS[idx - 2][2] if idx >= 2 else 0


# ─── Allègement des sources pour l'extérieur ──────────────────────────────────
_PUBLIC_SOURCE_FIELDS = ("title", "url", "snippet", "source_domain", "score", "reason", "query")


def public_sources(sources: List[Dict[str, Any]], snippet_max: int = 300) -> List[Dict[str, Any]]:
    """Projette les sources sur les champs publiables.

    Retire impérativement `content` / `raw_html` : les entrées FiscalOnline et
    JusticeLibre embarquent des articles entiers (plusieurs dizaines de Ko) qui
    n'ont rien à faire dans une réponse HTTP.
    """
    out = []
    for src in sources or []:
        item = {k: src[k] for k in _PUBLIC_SOURCE_FIELDS if k in src}
        snippet = item.get("snippet")
        if isinstance(snippet, str) and len(snippet) > snippet_max:
            item["snippet"] = snippet[:snippet_max] + "…"
        if src.get("_jl_source"):
            item["provider"] = src["_jl_source"]
        out.append(item)
    return out
