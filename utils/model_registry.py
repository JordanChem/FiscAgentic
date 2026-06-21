"""
Registre central des modèles LLM (mapping nom logique → identifiant LiteLLM).

Tous les agents utilisent un nom *logique* (ex. "gemini-3-flash-preview", "gpt-4o",
"claude-opus-4-8"). Ce module traduit ce nom logique en identifiant LiteLLM préfixé
par le provider (ex. "gemini/gemini-3-flash-preview", "openai/gpt-4o",
"anthropic/claude-opus-4-8") que `litellm.completion` sait router.

Il enregistre aussi un tarif custom pour les modèles que la table de prix interne de
LiteLLM ne connaît pas encore (modèles preview, gpt-5.2…), sans quoi le coût calculé
serait 0 et fausserait la comparaison de modèles (objectif 2).
"""
import logging

logger = logging.getLogger(__name__)

# ─── Mapping nom logique → identifiant LiteLLM (provider/model_id) ────────────
# La clé est le nom utilisé partout dans le code (DEFAULT_MODELS, sidebar, configs
# d'éval). La valeur est ce qui est passé à litellm.completion(model=...).
MODEL_REGISTRY = {
    # Google Gemini
    "gemini-3-pro-preview":   "gemini/gemini-3-pro-preview",
    "gemini-3-flash-preview": "gemini/gemini-3-flash-preview",
    "gemini-2.5-flash":       "gemini/gemini-2.5-flash",
    "gemini-2.5-pro":         "gemini/gemini-2.5-pro",
    # OpenAI
    "gpt-5.2": "openai/gpt-5.2-2025-12-11",
    "gpt-4o":  "openai/gpt-4o",
    # Anthropic (ajoutés pour la comparaison de providers)
    "claude-opus-4-8":   "anthropic/claude-opus-4-8",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
    "claude-haiku-4-5":  "anthropic/claude-haiku-4-5",
}

# ─── Tarifs custom (USD par token) pour les modèles absents de la table LiteLLM ─
# ⚠️ À VÉRIFIER / AJUSTER selon vos tarifs réels avant toute analyse de coût.
# Format : nom logique → {"input_cost_per_token": float, "output_cost_per_token": float}
# Laisser une entrée commentée si vous ne connaissez pas le prix (coût=0 + warning).
# (gpt-4o, gemini-2.5-flash sont déjà connus de LiteLLM → pas besoin de les lister.)
CUSTOM_PRICING = {
    # Exemples — remplacer par les tarifs officiels (USD / token = $/Mtok ÷ 1_000_000) :
    # "gpt-5.2":                 {"input_cost_per_token": 0.00000125, "output_cost_per_token": 0.00001},
    # "gemini-3-pro-preview":    {"input_cost_per_token": 0.00000125, "output_cost_per_token": 0.00001},
    # "gemini-3-flash-preview":  {"input_cost_per_token": 0.0000003,  "output_cost_per_token": 0.0000025},
}


def resolve_model(logical_name: str) -> str:
    """Retourne l'identifiant LiteLLM pour un nom logique.

    Si le nom n'est pas dans le registre, on le renvoie tel quel (permet de passer
    directement un id LiteLLM valide, ex. "openai/gpt-4o-mini").
    """
    resolved = MODEL_REGISTRY.get(logical_name)
    if resolved is None:
        logger.debug("model_registry — nom non répertorié, passé tel quel : %s", logical_name)
        return logical_name
    return resolved


def provider_of(logical_name: str) -> str:
    """Retourne le provider ('gemini' | 'openai' | 'anthropic' | …) d'un nom logique."""
    return resolve_model(logical_name).split("/", 1)[0]


def register_custom_pricing() -> None:
    """Enregistre les tarifs custom auprès de LiteLLM (idempotent).

    Appelé une fois à l'import de utils.llm. Sans cela, `litellm.completion_cost`
    renvoie 0 pour les modèles preview inconnus de la table interne.
    """
    if not CUSTOM_PRICING:
        return
    try:
        import litellm
        for logical_name, prices in CUSTOM_PRICING.items():
            litellm_id = resolve_model(logical_name)
            litellm.register_model({
                litellm_id: {
                    "input_cost_per_token":  prices["input_cost_per_token"],
                    "output_cost_per_token": prices["output_cost_per_token"],
                    "litellm_provider": provider_of(logical_name),
                    "mode": "chat",
                }
            })
            logger.info("model_registry — tarif custom enregistré pour %s", litellm_id)
    except Exception as exc:  # pragma: no cover - dépend de litellm installé
        logger.warning("model_registry — échec enregistrement tarifs custom : %s", exc)
