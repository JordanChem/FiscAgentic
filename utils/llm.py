"""
Couche d'abstraction LLM unique, basée sur LiteLLM.

Tous les agents passent par `llm_call` / `llm_call_stream` au lieu d'instancier
directement `google.generativeai` ou `openai`. Avantages :

- **un seul point** pour router Gemini / OpenAI / Anthropic (via le registre de modèles) ;
- capture **tokens / coût / latence** par appel (objectif 2 : comparaison de modèles) ;
- **tracing Langfuse** automatique (callback LiteLLM) si les clés sont présentes ;
- groupement des appels d'une même question sous une **trace unique** via `llm_trace`.

Les agents gardent leur signature actuelle (`api_key`, `model_name`) : ils délèguent
juste l'appel réseau ici.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, List, Dict, Optional

import litellm

from utils.model_registry import resolve_model, provider_of, register_custom_pricing

logger = logging.getLogger(__name__)

# LiteLLM est verbeux et lève parfois sur des champs de réponse manquants : on
# préfère renvoyer ce qu'on a plutôt que crasher le pipeline.
litellm.drop_params = True  # ignore les params non supportés par un provider donné

# Budgets d'appel. Sans eux, un appel qui ne rend jamais la main immobilise un
# slot de pipeline indéfiniment : un run de recette a vu l'orchestrateur bloqué
# 290 s sur un seul appel — un délai que rien n'aurait interrompu.
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "240"))
LLM_STREAM_TIMEOUT_S = float(os.getenv("LLM_STREAM_TIMEOUT_S", "180"))
# Une seule reprise : à 2 reprises, le pire cas d'un seul agent (3 × 90 s)
# consommerait à lui seul la moitié du budget global de la requête.
LLM_NUM_RETRIES = int(os.getenv("LLM_NUM_RETRIES", "1"))

# ─── Init tarifs custom + callback Langfuse (une seule fois) ──────────────────
_INITIALISED = False


def _init_once() -> None:
    global _INITIALISED
    if _INITIALISED:
        return
    register_custom_pricing()
    # Active le tracing Langfuse uniquement si les clés sont configurées.
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        try:
            cbs = set(litellm.success_callback or [])
            cbs.add("langfuse")
            litellm.success_callback = list(cbs)
            fail = set(litellm.failure_callback or [])
            fail.add("langfuse")
            litellm.failure_callback = list(fail)
            logger.info("llm — tracing Langfuse activé (callback LiteLLM)")
        except Exception as exc:  # pragma: no cover
            logger.warning("llm — échec activation Langfuse : %s", exc)
    _INITIALISED = True


# ─── Client Langfuse (SDK v2) pour l'instrumentation manuelle des étapes ──────
# Le callback LiteLLM trace automatiquement les appels LLM ; le client SDK sert à
# créer la trace parente + attacher des spans/événements aux étapes non-LLM
# (recherche, dédup, scraping) et aux artefacts structurés (scores, sources…).
_LANGFUSE_CLIENT = None
_LANGFUSE_TRIED = False


def _get_langfuse():
    """Renvoie un client Langfuse singleton, ou None si non configuré/indisponible.

    Langfuse est épinglé en v2 (compat callback LiteLLM + API Datasets de l'éval).
    """
    global _LANGFUSE_CLIENT, _LANGFUSE_TRIED
    if _LANGFUSE_TRIED:
        return _LANGFUSE_CLIENT
    _LANGFUSE_TRIED = True
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import Langfuse  # import paresseux : dépendance optionnelle
        _LANGFUSE_CLIENT = Langfuse()
        logger.info("llm — client Langfuse (SDK v2) initialisé")
    except Exception as exc:  # pragma: no cover
        logger.warning("llm — client Langfuse indisponible : %s", exc)
        _LANGFUSE_CLIENT = None
    return _LANGFUSE_CLIENT


# ─── Contexte de run (groupe les appels d'une question sous une trace) ────────
@dataclass
class CallRecord:
    """Métriques d'un appel LLM unitaire."""
    agent: str
    model: str          # nom logique
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float


@dataclass
class RunContext:
    trace_id: str
    session_id: Optional[str] = None
    config_name: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    records: List[CallRecord] = field(default_factory=list)
    # Objet trace Langfuse (SDK v2) associé — permet d'attacher des spans/événements
    # aux étapes non-LLM. `None` si Langfuse n'est pas configuré.
    lf_trace: object = None

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records)

    def cost_by_agent(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for r in self.records:
            out[r.agent] = out.get(r.agent, 0.0) + r.cost_usd
        return out


_run_ctx: ContextVar[Optional[RunContext]] = ContextVar("llm_run_ctx", default=None)


@contextmanager
def llm_trace(trace_id: Optional[str] = None, session_id: Optional[str] = None,
              config_name: Optional[str] = None, tags: Optional[List[str]] = None,
              name: Optional[str] = None, user_id: Optional[str] = None,
              input: Optional[object] = None):
    """Groupe tous les `llm_call` du bloc sous une même trace Langfuse + collecte
    les métriques par appel. Utilisé par le pipeline (un bloc = une question).

    Crée aussi l'objet trace Langfuse parent (si configuré) pour permettre
    `trace_step` / `finalize_trace`. Le callback LiteLLM rattache ses générations
    à la même trace via l'`id` partagé.
    """
    _init_once()  # garantit l'enregistrement du callback Langfuse de LiteLLM
    ctx = RunContext(
        trace_id=trace_id or f"fisca-{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        config_name=config_name,
        tags=list(tags or []),
    )
    lf = _get_langfuse()
    if lf is not None:
        try:
            ctx.lf_trace = lf.trace(
                id=ctx.trace_id,
                name=name or "fisca-run",
                session_id=session_id,
                user_id=user_id,
                input=input,
                tags=ctx.tags or None,
                metadata={"config_name": config_name} if config_name else None,
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("llm — création trace Langfuse échouée : %s", exc)
    token = _run_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _run_ctx.reset(token)


def current_run() -> Optional[RunContext]:
    return _run_ctx.get()


def trace_step(name: str, *, input: Optional[object] = None,
               output: Optional[object] = None, metadata: Optional[dict] = None,
               level: Optional[str] = None) -> None:
    """Attache un événement (étape non-LLM / artefact structuré) à la trace courante.

    No-op si aucune trace n'est active ou si Langfuse n'est pas configuré.
    L'observabilité ne doit jamais casser le pipeline → tout est encapsulé.
    """
    ctx = _run_ctx.get()
    if ctx is None or ctx.lf_trace is None:
        return
    try:
        kwargs = {"name": name, "input": input, "output": output, "metadata": metadata}
        if level is not None:
            kwargs["level"] = level
        ctx.lf_trace.event(**kwargs)
    except Exception as exc:  # pragma: no cover
        logger.debug("llm — trace_step '%s' échoué : %s", name, exc)


def finalize_trace(output: Optional[object] = None,
                   metadata: Optional[dict] = None) -> None:
    """Finalise la trace courante : renseigne l'`output` + les métriques agrégées,
    puis force l'envoi (`flush`). À appeler tant que la trace est encore active
    (dans le bloc `with llm_trace(...)`). No-op si Langfuse absent.
    """
    ctx = _run_ctx.get()
    if ctx is None:
        return
    if ctx.lf_trace is not None:
        try:
            md = {
                "total_cost_usd": ctx.total_cost,
                "total_input_tokens": ctx.total_input_tokens,
                "total_output_tokens": ctx.total_output_tokens,
                "cost_by_agent": ctx.cost_by_agent(),
            }
            if metadata:
                md.update(metadata)
            ctx.lf_trace.update(output=output, metadata=md)
        except Exception as exc:  # pragma: no cover
            logger.debug("llm — finalize_trace échoué : %s", exc)
    lf = _get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception:  # pragma: no cover
            pass


# ─── Réponse normalisée ───────────────────────────────────────────────────────
@dataclass
class LLMResponse:
    text: str
    model: str          # nom logique
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    raw: object = None


# ─── Helpers internes ─────────────────────────────────────────────────────────
def _coerce_text(value) -> str:
    """Coerce un prompt en chaîne. Défensif : certains prompts d'agents sont en
    réalité des tuples/listes (concaténation de littéraux avec une virgule parasite) ;
    l'ancien SDK Gemini les tolérait, pas LiteLLM. On les rejoint ici."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "".join(str(v) for v in value)
    return str(value)


def _build_messages(prompt, messages: Optional[List[Dict]],
                    system: Optional[str]) -> List[Dict]:
    if messages is not None:
        return messages
    msgs: List[Dict] = []
    if system:
        msgs.append({"role": "system", "content": _coerce_text(system)})
    msgs.append({"role": "user", "content": _coerce_text(prompt)})
    return msgs


def _langfuse_metadata(agent_name: str) -> dict:
    ctx = _run_ctx.get()
    md = {"generation_name": agent_name}
    if ctx:
        md["trace_id"] = ctx.trace_id
        if ctx.session_id:
            md["session_id"] = ctx.session_id
        if ctx.tags:
            md["tags"] = ctx.tags
        md["trace_metadata"] = {"config_name": ctx.config_name}
    return md


def _extract_cost(response, litellm_id: str) -> float:
    """Coût USD de l'appel : priorité aux hidden_params, sinon completion_cost."""
    try:
        hp = getattr(response, "_hidden_params", None) or {}
        cost = hp.get("response_cost")
        if cost is not None:
            return float(cost)
    except Exception:
        pass
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        logger.debug("llm — coût indisponible pour %s (tarif non enregistré ?)", litellm_id)
        return 0.0


def _record(agent_name: str, logical_model: str, response, latency_s: float) -> LLMResponse:
    litellm_id = resolve_model(logical_model)
    usage = getattr(response, "usage", None)
    in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
    out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
    cost = _extract_cost(response, litellm_id)
    provider = provider_of(logical_model)

    ctx = _run_ctx.get()
    if ctx is not None:
        ctx.records.append(CallRecord(
            agent=agent_name, model=logical_model, provider=provider,
            input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=cost, latency_s=latency_s,
        ))

    text = ""
    try:
        text = response.choices[0].message.content or ""
    except Exception:
        logger.warning("llm — réponse sans contenu exploitable (%s)", litellm_id)

    return LLMResponse(
        text=text, model=logical_model, provider=provider,
        input_tokens=in_tok, output_tokens=out_tok,
        cost_usd=cost, latency_s=latency_s, raw=response,
    )


def _resolve_api_key(provider: str, fallback: Optional[str] = None) -> Optional[str]:
    """Clé API correspondant au PROVIDER du modèle (et non au provider d'origine de
    l'agent). Indispensable quand on bascule un agent vers un autre provider : sinon
    on enverrait la mauvaise clé (ex. clé Google → API Anthropic → 401).
    """
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY") or fallback
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY") or fallback
    if provider == "gemini":
        return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or fallback
    return fallback


# ─── API publique ─────────────────────────────────────────────────────────────
def llm_call(
    model_name: str,
    *,
    prompt: Optional[str] = None,
    messages: Optional[List[Dict]] = None,
    system: Optional[str] = None,
    temperature: float = 0.0,
    json_mode: bool = False,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    agent_name: str = "llm_call",
) -> LLMResponse:
    """Appel LLM non-streamé via LiteLLM.

    Args:
        model_name: nom logique (résolu via le registre).
        prompt / messages / system: contenu (prompt simple OU messages explicites).
        json_mode: force une sortie JSON (response_format json_object,
            mappé en response_mime_type côté Gemini par LiteLLM).
        api_key: clé du provider (sinon LiteLLM lit l'env adéquat).
        agent_name: libellé de l'agent (pour le tracing + l'agrégation des coûts).
    """
    _init_once()
    litellm_id = resolve_model(model_name)
    kwargs = {
        "model": litellm_id,
        "messages": _build_messages(prompt, messages, system),
        "temperature": temperature,
        "metadata": _langfuse_metadata(agent_name),
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    kwargs["timeout"] = LLM_TIMEOUT_S
    kwargs["num_retries"] = LLM_NUM_RETRIES
    # Clé choisie selon le PROVIDER du modèle (et non l'api_key passé par l'agent,
    # qui correspond au provider d'origine et serait faux après bascule de modèle).
    resolved_key = _resolve_api_key(provider_of(model_name), api_key)
    if resolved_key:
        kwargs["api_key"] = resolved_key

    logger.info("%s — appel LLM (%s)", agent_name, litellm_id)
    t0 = time.time()
    response = litellm.completion(**kwargs)
    latency = time.time() - t0
    res = _record(agent_name, model_name, response, latency)
    logger.info("%s — réponse (%.1fs, in=%d out=%d, $%.5f)",
                agent_name, latency, res.input_tokens, res.output_tokens, res.cost_usd)
    return res


def llm_call_stream(
    model_name: str,
    *,
    prompt: Optional[str] = None,
    messages: Optional[List[Dict]] = None,
    system: Optional[str] = None,
    temperature: float = 0.0,
    json_mode: bool = False,
    api_key: Optional[str] = None,
    agent_name: str = "llm_call_stream",
) -> Iterator[str]:
    """Version streamée : yield les chunks de texte au fil de l'eau.

    Les métriques (tokens/coût/latence) sont enregistrées en fin de stream via
    `litellm.stream_chunk_builder` (usage demandé avec stream_options).
    """
    _init_once()
    litellm_id = resolve_model(model_name)
    kwargs = {
        "model": litellm_id,
        "messages": _build_messages(prompt, messages, system),
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        "metadata": _langfuse_metadata(agent_name),
        # Budget plus large qu'un appel bloquant : la rédaction produit un
        # document long, et le timeout porte sur l'inactivité du flux.
        "timeout": LLM_STREAM_TIMEOUT_S,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resolved_key = _resolve_api_key(provider_of(model_name), api_key)
    if resolved_key:
        kwargs["api_key"] = resolved_key

    logger.info("%s — appel LLM stream (%s)", agent_name, litellm_id)
    t0 = time.time()
    chunks = []
    response = litellm.completion(**kwargs)
    for chunk in response:
        chunks.append(chunk)
        try:
            delta = chunk.choices[0].delta.content
        except Exception:
            delta = None
        if delta:
            yield delta

    latency = time.time() - t0
    # Reconstruit la réponse complète pour récupérer usage + coût.
    try:
        rebuilt = litellm.stream_chunk_builder(chunks, messages=kwargs["messages"])
        _record(agent_name, model_name, rebuilt, latency)
    except Exception as exc:
        logger.debug("%s — usage stream indisponible : %s", agent_name, exc)
    logger.info("%s — stream terminé (%.1fs, %d chunks)", agent_name, latency, len(chunks))
