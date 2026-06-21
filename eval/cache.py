"""
Cache disque des exécutions du pipeline.

Une exécution du pipeline est COÛTEUSE (recherche web + scraping + ~9 appels LLM).
On met donc en cache le `PipelineResult` par clé = hash(question + config de modèles +
use_justicelibre). Cela permet de ré-évaluer / d'ajuster les métriques (qui, elles,
sont rapides) sans relancer tout le pipeline.

Même esprit que le `bofip_cache/` existant. Dossier : eval/.cache/ (gitignoré).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from typing import Dict, Optional

from pipeline.core import run_pipeline, PipelineResult

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")


def _key(question: str, models_config: Dict[str, str], use_justicelibre: bool) -> str:
    payload = json.dumps(
        {"q": question, "m": dict(sorted(models_config.items())), "jl": use_justicelibre},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def run_pipeline_cached(
    question: str,
    models_config: Dict[str, str],
    use_justicelibre: bool = True,
    config_name: Optional[str] = None,
    force: bool = False,
) -> PipelineResult:
    """Comme `run_pipeline`, mais sert le cache disque si disponible.

    Args:
        force: ignore le cache et recalcule (puis réécrit).
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _key(question, models_config, use_justicelibre)
    path = _path(key)

    if not force and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            logger.info("cache HIT — %s (%s)", key, config_name or "default")
            return PipelineResult(**data)
        except Exception as exc:
            logger.warning("cache illisible (%s), recalcul : %s", path, exc)

    logger.info("cache MISS — exécution pipeline (%s)", config_name or "default")
    result = run_pipeline(question, models_config, use_justicelibre=use_justicelibre,
                          config_name=config_name)
    # On ne met en cache que les exécutions réussies (réponse non vide, pas d'erreur).
    if not result.error and result.answer_text:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(result), f, ensure_ascii=False)
        except Exception as exc:
            logger.warning("échec écriture cache (%s) : %s", path, exc)
    return result


def clear_cache() -> int:
    """Vide le cache. Retourne le nombre de fichiers supprimés."""
    if not os.path.isdir(CACHE_DIR):
        return 0
    n = 0
    for fn in os.listdir(CACHE_DIR):
        if fn.endswith(".json"):
            os.remove(os.path.join(CACHE_DIR, fn))
            n += 1
    return n
