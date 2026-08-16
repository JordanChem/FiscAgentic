"""
Configurations de modèles nommées, pour la comparaison (objectif 2).

Chaque config est un dict {agent: nom_logique} fusionné avec DEFAULT_MODELS du
pipeline. On compare ensuite ces configs sur qualité × coût × latence.

Agents disponibles (clés) : analyste, orchestrateur, specialises, verificateur,
generaliste, jurisprudence, ranker, redactionnel.
Les noms logiques doivent exister dans utils/model_registry.MODEL_REGISTRY.
"""
# Base de référence de l'évaluation, **figée volontairement**.
#
# Elle valait autrefois `pipeline.core.DEFAULT_MODELS`. Depuis que ce dict porte
# la configuration de *production* (Claude), l'importer ferait dériver toutes les
# configs à chaque changement de modèle en prod — et comme `eval/cache.py` hashe
# `models_config`, cela invaliderait tout le cache disque et imposerait un
# recalcul complet du golden set. La base d'éval est donc indépendante : la faire
# évoluer devient une décision explicite.
DEFAULT_MODELS = {
    "analyste":      "gemini-2.5-flash",
    "generaliste":   "gpt-4o",
    "jurisprudence": "gemini-2.5-flash",
    "orchestrateur": "gpt-4o",
    "ranker":        "gpt-4o",
    "redactionnel":  "gemini-2.5-flash",
    "specialises":   "gemini-2.5-flash",
    "verificateur":  "gemini-2.5-flash",
}

# Sous-ensembles d'agents par provider (pour construire des configs homogènes).
_GEMINI_AGENTS = ["analyste", "specialises", "verificateur", "jurisprudence", "redactionnel"]
_OPENAI_AGENTS = ["orchestrateur", "generaliste", "ranker"]


def _all(model: str) -> dict:
    """Config homogène : tous les agents sur le même modèle (override total)."""
    return {agent: model for agent in DEFAULT_MODELS}


CONFIGS = {
    # Référence actuelle (Gemini 2.5 Flash + GPT-4o)
    "baseline": {**DEFAULT_MODELS, 
                       "redactionnel": "claude-opus-4-8",
                       "analyste": "claude-sonnet-4-6",
                       "orchestrateur": "claude-sonnet-4-6",
                       "specialises": "claude-sonnet-4-6",
                       "verificateur": "claude-sonnet-4-6",
                       "generaliste": "claude-sonnet-4-6",
                       "jurisprudence": "claude-sonnet-4-6",
                       "ranker": "gpt-4o"},

    # Familles Gemini 3
    "gemini3-flash": {**DEFAULT_MODELS, **{a: "gemini-3-flash-preview" for a in _GEMINI_AGENTS}},
    "gemini3-pro":   {**DEFAULT_MODELS, **{a: "gemini-3-pro-preview" for a in _GEMINI_AGENTS}},

    # OpenAI nouvelle génération sur les agents OpenAI
    "gpt5": {**DEFAULT_MODELS, **{a: "gpt-5.2" for a in _OPENAI_AGENTS}},

    # Anthropic sur les agents génératifs (le reste inchangé)
    "claude": {**DEFAULT_MODELS, **{
        "analyste": "claude-sonnet-4-6",
        "specialises": "claude-sonnet-4-6",
        "verificateur": "claude-sonnet-4-6",
        "redactionnel": "claude-opus-4-8",
    }},

    # Tout Claude (comparaison de provider unique)
    "all-claude": _all("claude-sonnet-4-6"),
}

CONFIGS["Quality_over_price"] = {**DEFAULT_MODELS, 
                       "redactionnel": "claude-opus-4-8",
                       "analyste": "claude-sonnet-4-6",
                       "orchestrateur": "claude-sonnet-4-6",
                       "specialises": "claude-sonnet-4-6",
                       "verificateur": "claude-sonnet-4-6",
                       "generaliste": "claude-sonnet-4-6",
                       "jurisprudence": "claude-sonnet-4-6",
                       "ranker": "gpt-4o"}

CONFIGS["Price_over_quality"] = {**DEFAULT_MODELS, 
                       "redactionnel": "gemini-2.5-flash",
                       "analyste": "gemini-2.5-flash",
                       "orchestrateur": "gpt-4o",
                       "specialises": "gemini-2.5-flash",
                       "verificateur": "gemini-2.5-flash",
                       "generaliste": "gpt-4o",
                       "jurisprudence": "gemini-2.5-flash",
                       "ranker": "gpt-4o"}


def get_config(name: str) -> dict:
    if name not in CONFIGS:
        raise KeyError(f"Config inconnue : {name}. Disponibles : {list(CONFIGS)}")
    return CONFIGS[name]
