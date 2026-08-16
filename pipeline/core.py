"""
Pipeline fiscal — **un seul** générateur, deux points d'entrée.

- `run_pipeline_stream(question, ...) -> Iterator[PipelineEvent]`
  émet la progression étape par étape, les sources, puis les fragments de la
  réponse rédigée, et termine par un `ResultEvent` portant le `PipelineResult`.
  C'est ce que consomment l'API SSE et l'UI Streamlit de debug.

- `run_pipeline(question, ...) -> PipelineResult`
  draine le générateur avec `stream_redaction=False` (le rédactionnel est alors
  appelé en mode bloquant `json_mode=True`, comme historiquement). C'est ce que
  consomment `eval/` et `test_pipeline.py`.

Il n'existe donc **qu'une** implémentation des 11 étapes : c'est ce qui empêche
la ré-apparition de la divergence entre l'app Streamlit et le pipeline headless.

Toute l'exécution est enveloppée dans une trace LLM (`utils.llm.llm_trace`) qui
agrège coût / tokens / latence par agent (et trace Langfuse si configuré).
"""
from __future__ import annotations

import ast
import logging
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed,
)
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from agents.analyste import agent_analyste
from agents.orchestrateur import agent_orchestrateur
from agents.specialises import (
    agent_particulier_revenu, agent_tva_indirect, agent_entreprise_is,
    agent_patrimoine_transmission, agent_structure_montage, agent_international,
    agent_droit_europeen, agent_immobilier_urbanisme, agent_procedure_contentieux,
    agent_taxes_locales, agent_prelevements_sociaux,
)
from agents.generaliste import agent_generaliste
from agents.verificateur import agent_verificateur
from agents.jurisprudence_dork import generate_jurisprudence_dork
from agents.ranker import agent_ranker
from agents.redactionnel import agent_redactionnel, agent_redactionnel_stream
from pipeline.errors import PipelineCancelled, PipelineDeadlineExceeded
from pipeline.events import (
    PipelineEvent, ResultEvent, SourcesEvent, TextDelta,
    public_sources, step_finished, step_started,
)
from pipeline.normalizer import RedactionNormalizer
from utils.api_keys import get_api_keys
from utils.json_utils import clean_json_codefence, lire_json_beton
from utils.llm import finalize_trace, llm_trace, trace_step
from utils.scraper_utils import scrapper
from utils.search import OFFICIAL_DOMAINS, search_with_fallback

logger = logging.getLogger(__name__)

AGENT_FUNCTIONS = {
    "AGENT_PARTICULIERS_REVENUS":    agent_particulier_revenu,
    "AGENT_TVA_INDIRECTES":          agent_tva_indirect,
    "AGENT_ENTREPRISES_IS":          agent_entreprise_is,
    "AGENT_PATRIMOINE_TRANSMISSION": agent_patrimoine_transmission,
    "AGENT_STRUCTURES_MONTAGES":     agent_structure_montage,
    "AGENT_INTERNATIONAL":           agent_international,
    "AGENT_DROIT_EUROPEEN":          agent_droit_europeen,
    "AGENT_IMMOBILIER_URBANISME":    agent_immobilier_urbanisme,
    "AGENT_PROCEDURE_CONTENTIEUX":   agent_procedure_contentieux,
    "AGENT_TAXES_LOCALES":           agent_taxes_locales,
    "AGENT_PRELEVEMENTS_SOCIAUX":    agent_prelevements_sociaux,
}

# Configuration de modèles de **production** (nom logique par agent).
# Source unique : l'UI Streamlit et l'API lisent toutes les deux ce dict.
# Note : `eval/configs.py` fige délibérément sa propre base (`_EVAL_BASE`) pour
# que faire évoluer la prod n'invalide pas le cache d'évaluation.
DEFAULT_MODELS: Dict[str, str] = {
    "analyste":      "claude-sonnet-4-6",
    "orchestrateur": "claude-sonnet-4-6",
    "specialises":   "claude-sonnet-4-6",
    "verificateur":  "claude-sonnet-4-6",
    "generaliste":   "claude-sonnet-4-6",
    "jurisprudence": "claude-sonnet-4-6",
    "ranker":        "gpt-4o",
    "redactionnel":  "claude-opus-4-8",
    "suivi":         "gemini-3-flash-preview",
}

# Seuils de conservation du ranker (repli si le seuil haut ne rend rien).
RANK_KEEP_THRESHOLD = 0.8
RANK_FALLBACK_THRESHOLD = 0.6

# Budget d'attente des agents spécialisés : au-delà, on rédige avec ceux qui ont
# répondu plutôt que de bloquer la requête entière sur un agent en souffrance.
SPECIALISTS_TIMEOUT_S = 120.0
FISCALONLINE_TIMEOUT_S = 60.0

HORS_PERIMETRE = (
    "**Ce type de question ne relève pas des domaines fiscaux couverts par cet assistant.**\n\n"
    "L'assistant fiscal traite uniquement les sujets suivants : impôt sur le revenu, TVA, "
    "impôt sur les sociétés, patrimoine et transmission, fiscalité internationale, "
    "immobilier, procédure fiscale, taxes locales et prélèvements sociaux.\n\n"
    "Merci de reformuler votre question dans ce cadre."
)


@dataclass
class TraceOptions:
    """Métadonnées d'observabilité rattachées à l'exécution."""
    session_id: Optional[str] = None    # id de conversation → regroupement Langfuse
    user_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Sortie structurée d'une exécution du pipeline pour une question."""
    question: str
    answer_text: str
    points_cles: List[str]
    analyste: dict
    sources: List[dict]            # sources retenues (ranked keep), sans contenu lourd
    scraped_context: List[str]     # contenus scrapés (retrieval_context faithfulness)
    selected_agents: List[str]
    # ── Métriques (comparaison qualité × coût × latence) ──
    config_name: Optional[str] = None
    trace_id: Optional[str] = None      # id de la trace Langfuse (pour attacher des scores)
    models_config: dict = field(default_factory=dict)
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    wall_clock_s: float = 0.0
    cost_by_agent: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)        # secondes par étape
    n_sources_raw: int = 0
    n_sources_kept: int = 0
    raw_response: dict = field(default_factory=dict)
    error: Optional[str] = None
    # ── Champs ajoutés pour l'API (tous avec défaut : `eval/cache.py` relit
    #    des PipelineResult sérialisés avant leur existence) ──
    fiscalonline_count: int = 0
    cancelled: bool = False
    is_follow_up: bool = False


# ─── Point d'entrée streamé ───────────────────────────────────────────────────
def run_pipeline_stream(
    question: str,
    *,
    models_config: Optional[Dict[str, str]] = None,
    active_domains: Optional[List[str]] = None,
    use_justicelibre: bool = True,
    use_fiscalonline: Optional[bool] = None,
    stream_redaction: bool = True,
    config_name: Optional[str] = None,
    trace: Optional[TraceOptions] = None,
    cancel: Optional[threading.Event] = None,
    deadline_s: Optional[float] = None,
) -> Iterator[PipelineEvent]:
    """Exécute le pipeline en émettant sa progression.

    Args:
        question: la question fiscale.
        models_config: override {agent: nom_logique}, fusionné avec DEFAULT_MODELS.
        active_domains: domaines officiels autorisés (défaut = OFFICIAL_DOMAINS).
        use_justicelibre: active la recherche JusticeLibre (CE / Cass / CJUE).
        use_fiscalonline: articles internes FiscalOnline. None → déduit de
            `active_domains` (comportement de l'app historique).
        stream_redaction: True → `agent_redactionnel_stream` + `TextDelta` ;
            False → `agent_redactionnel` bloquant (chemin d'évaluation).
        config_name: étiquette de la config (tracing / comparaison).
        trace: session_id / user_id / tags Langfuse.
        cancel: `threading.Event` — testé aux frontières d'étape et à chaque
            fragment de rédaction ; lève `PipelineCancelled`.
        deadline_s: budget de temps global en secondes.

    Yields:
        StepEvent, SourcesEvent, TextDelta, puis un ResultEvent final.

    Raises:
        PipelineCancelled / PipelineDeadlineExceeded si l'exécution est
        interrompue (la trace est finalisée avant de propager).
    """
    models = {**DEFAULT_MODELS, **(models_config or {})}
    active_domains = list(active_domains) if active_domains is not None else OFFICIAL_DOMAINS.copy()
    if use_fiscalonline is None:
        use_fiscalonline = "fiscalonline.fr" in active_domains
    trace = trace or TraceOptions()

    openai_key, google_key, serpapi_key = get_api_keys()
    missing = [n for n, k in (("OPENAI_API_KEY", openai_key), ("GOOGLE_API_KEY", google_key),
                              ("SERPAPI_API_KEY", serpapi_key)) if not k]
    if missing:
        raise RuntimeError(f"Clés API manquantes : {', '.join(missing)}")

    timings: Dict[str, float] = {}
    t_total = time.time()
    deadline = (t_total + deadline_s) if deadline_s else None

    def _checkpoint(step: str) -> None:
        if cancel is not None and cancel.is_set():
            raise PipelineCancelled(step)
        if deadline is not None and time.time() > deadline:
            raise PipelineDeadlineExceeded(step, deadline_s)

    # État accumulé, lu par la construction du résultat / des replis d'erreur.
    analyst_json: dict = {}
    selected_agents: List[str] = []
    ranked_keep: List[dict] = []
    doc_fiscalonline: List[dict] = []
    doc_enriched: List[dict] = []
    structured_results: List[dict] = []
    normalizer = RedactionNormalizer()
    raw_answer = ""

    fisca_executor: Optional[ThreadPoolExecutor] = None
    fisca_future = None

    logger.info("PIPELINE START — question: %r", question[:120])

    with llm_trace(
        name="fisca-question", input=question,
        session_id=trace.session_id, user_id=trace.user_id,
        tags=trace.tags or ["pipeline", config_name or "default"],
        config_name=config_name,
    ) as ctx:
        finalized = False
        try:
            # ── 1. Analyste ──────────────────────────────────────────────────
            _checkpoint("analyse")
            yield step_started("analyse")
            t0 = time.time()
            result_analyste = agent_analyste(question, google_key, model_name=models["analyste"])
            analyst_json = lire_json_beton(result_analyste)
            timings["analyste"] = time.time() - t0
            yield step_finished("analyse", timings["analyste"], chars=len(result_analyste or ""))

            # ── 1b. FiscalOnline en parallèle ────────────────────────────────
            # copy_context() propage la trace LLM courante au worker : sans elle,
            # la ContextVar ne franchit pas la frontière de thread et les appels
            # LLM de FiscalOnline seraient orphelins (coût non agrégé).
            if use_fiscalonline:
                from utils.fiscalonline import main_fiscalonline
                fisca_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fiscalonline")
                fisca_future = fisca_executor.submit(
                    copy_context().run, main_fiscalonline, question, result_analyste, openai_key
                )
                yield step_started("fiscalonline")

            # ── 2. Orchestrateur ─────────────────────────────────────────────
            _checkpoint("routage")
            yield step_started("routage")
            t0 = time.time()
            routing = lire_json_beton(
                agent_orchestrateur(question, result_analyste, openai_key,
                                    model_name=models["orchestrateur"])
            )
            selected_agents = routing.get("selected_agents", [])
            scores = routing.get("scores", {})
            timings["orchestrateur"] = time.time() - t0
            trace_step("routage", output={"selected_agents": selected_agents, "scores": scores})
            yield step_finished("routage", timings["orchestrateur"], agents=selected_agents)

            valid_agents = [n for n in selected_agents if n in AGENT_FUNCTIONS]
            if not valid_agents:
                logger.warning("Aucun agent valide sélectionné — question hors périmètre fiscal")
                finalize_trace(output=HORS_PERIMETRE)
                finalized = True
                yield ResultEvent(PipelineResult(
                    question=question, answer_text=HORS_PERIMETRE,
                    points_cles=["Question hors périmètre fiscal couvert"],
                    analyste=analyst_json, sources=[], scraped_context=[], selected_agents=[],
                    config_name=config_name, trace_id=ctx.trace_id, models_config=models,
                    total_cost_usd=ctx.total_cost,
                    total_input_tokens=ctx.total_input_tokens,
                    total_output_tokens=ctx.total_output_tokens,
                    wall_clock_s=time.time() - t_total,
                    cost_by_agent=ctx.cost_by_agent(), timings=timings,
                ))
                return

            # ── 3. Agents spécialisés (parallèle) ────────────────────────────
            _checkpoint("specialistes")
            yield step_started("specialistes", agents=valid_agents)
            t0 = time.time()
            results: Dict[str, str] = {}

            def _call_specialist(name: str):
                return name, AGENT_FUNCTIONS[name](
                    question, result_analyste, google_key,
                    available_domain=active_domains, model_name=models["specialises"],
                )

            # Pas de `with` : son __exit__ attend TOUS les workers, ce qui annulerait
            # l'effet du budget ci-dessous. On rend la main dès le budget écoulé et
            # on laisse les retardataires mourir en arrière-plan (threads daemon).
            executor = ThreadPoolExecutor(max_workers=max(1, len(valid_agents)),
                                          thread_name_prefix="specialiste")
            try:
                # copy_context() doit être évalué ICI (thread principal), pas côté worker.
                futures = [executor.submit(copy_context().run, _call_specialist, n)
                           for n in valid_agents]
                try:
                    for future in as_completed(futures, timeout=SPECIALISTS_TIMEOUT_S):
                        name, res = future.result()
                        if res:
                            results[name] = res
                except FuturesTimeout:
                    logger.warning(
                        "Spécialistes — budget %ss dépassé, on poursuit avec %d/%d réponses",
                        SPECIALISTS_TIMEOUT_S, len(results), len(valid_agents),
                    )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            timings["specialises"] = time.time() - t0
            trace_step("specialistes", output={n: results.get(n) for n in valid_agents},
                       metadata={"repondants": list(results.keys()), "demandes": valid_agents})
            yield step_finished("specialistes", timings["specialises"],
                                repondants=len(results), demandes=len(valid_agents))

            # ── 4. Vérificateur ──────────────────────────────────────────────
            _checkpoint("verification")
            yield step_started("verification")
            t0 = time.time()
            verified_sources = lire_json_beton(
                agent_verificateur(question, result_analyste, results, google_key,
                                   model_name=models["verificateur"])
            )
            total_verified = sum(len(v) for v in verified_sources.values() if isinstance(v, list))
            timings["verificateur"] = time.time() - t0
            trace_step("verification", output=verified_sources,
                       metadata={"total_sources": total_verified})
            yield step_finished("verification", timings["verificateur"], sources=total_verified)

            # ── 5. Généraliste (requêtes de recherche) ───────────────────────
            _checkpoint("requetes")
            yield step_started("requetes")
            t0 = time.time()
            queries = agent_generaliste(question, openai_key, active_domains=active_domains,
                                        model_name=models["generaliste"])
            timings["generaliste"] = time.time() - t0
            trace_step("requetes_generaliste", output=queries, metadata={"n_requetes": len(queries)})
            yield step_finished("requetes", timings["generaliste"], n_requetes=len(queries))

            # ── 5b. Jurisprudence (Google Dork Cour de cassation) ────────────
            _checkpoint("jurisprudence")
            yield step_started("jurisprudence")
            t0 = time.time()
            jurisprudence_queries = _parse_dork_queries(
                generate_jurisprudence_dork(question, result_analyste, google_key,
                                            model_name=models["jurisprudence"])
            )
            timings["jurisprudence"] = time.time() - t0
            trace_step("requetes_jurisprudence", output=jurisprudence_queries,
                       metadata={"n_requetes": len(jurisprudence_queries)})
            yield step_finished("jurisprudence", timings["jurisprudence"],
                                n_requetes=len(jurisprudence_queries))

            # ── 6. Concaténation des requêtes ────────────────────────────────
            l_experts = [v for lst in verified_sources.values()
                         if isinstance(lst, list) for v in lst]
            full_queries = queries + l_experts + jurisprudence_queries
            logger.info("Requêtes totales: %d (%d généraliste + %d experts + %d jurisprudence)",
                        len(full_queries), len(queries), len(l_experts), len(jurisprudence_queries))

            # ── 7. Recherche (JusticeLibre MCP + SerpAPI) ────────────────────
            _checkpoint("recherche")
            yield step_started("recherche", n_requetes=len(full_queries))
            t0 = time.time()
            structured_results = search_with_fallback(
                full_queries, serpapi_key,
                active_domains=active_domains,
                use_justicelibre=use_justicelibre,
                analyst_json=analyst_json,
            )
            n_jl = sum(1 for r in structured_results if r.get("_jl_source") == "justicelibre")
            timings["search"] = time.time() - t0
            trace_step("recherche", metadata={
                "n_requetes": len(full_queries), "n_resultats_bruts": len(structured_results),
                "justicelibre": n_jl, "serpapi": len(structured_results) - n_jl,
                "use_justicelibre": use_justicelibre,
            })
            yield step_finished("recherche", timings["search"],
                                resultats=len(structured_results), justicelibre=n_jl)

            # ── 8. Déduplication ─────────────────────────────────────────────
            yield step_started("deduplication")
            seen, unique = set(), []
            for res in structured_results:
                url = res.get("url")
                if url and url not in seen:
                    unique.append(res)
                    seen.add(url)
            trace_step("deduplication", metadata={"avant": len(structured_results),
                                                  "apres": len(unique)})
            yield step_finished("deduplication", 0.0, avant=len(structured_results),
                                apres=len(unique))

            # ── 9. Ranking ───────────────────────────────────────────────────
            _checkpoint("ranking")
            yield step_started("ranking", candidats=len(unique))
            t0 = time.time()
            ranked = agent_ranker(question, unique, result_analyste, results, openai_key,
                                  model=models["ranker"])
            ranked_keep = [x for x in ranked
                           if x.get("keep") and x.get("score", 0) >= RANK_KEEP_THRESHOLD]
            if not ranked_keep:
                ranked_keep = [x for x in ranked
                               if x.get("keep") and x.get("score", 0) >= RANK_FALLBACK_THRESHOLD]
                logger.warning("Seuil %.1f → 0 résultat, repli %.1f → %d résultats",
                               RANK_KEEP_THRESHOLD, RANK_FALLBACK_THRESHOLD, len(ranked_keep))
            timings["ranker"] = time.time() - t0
            trace_step("ranking", output=[{"url": x.get("url"), "score": x.get("score"),
                                           "reason": x.get("reason")} for x in ranked_keep],
                       metadata={"candidats": len(unique), "retenues": len(ranked_keep)})
            yield step_finished("ranking", timings["ranker"],
                                candidats=len(unique), retenues=len(ranked_keep))

            # ── 10. Scraping ─────────────────────────────────────────────────
            _checkpoint("scraping")
            yield step_started("scraping", urls=len(ranked_keep))
            t0 = time.time()
            doc_enriched = scrapper(ranked_keep)
            n_ok = sum(1 for d in doc_enriched if d.get("content"))
            timings["scraping"] = time.time() - t0
            trace_step("scraping", metadata={"urls_avec_contenu": n_ok,
                                             "urls_total": len(doc_enriched)})
            yield step_finished("scraping", timings["scraping"],
                                avec_contenu=n_ok, total=len(doc_enriched))

            # ── 10b. Fusion des articles FiscalOnline ────────────────────────
            if fisca_future is not None:
                t0 = time.time()
                try:
                    doc_fiscalonline = fisca_future.result(timeout=FISCALONLINE_TIMEOUT_S) or []
                    doc_enriched = doc_fiscalonline + doc_enriched
                    yield step_finished("fiscalonline", time.time() - t0,
                                        articles=len(doc_fiscalonline))
                except Exception as exc:
                    logger.warning("FiscalOnline — récupération des articles échouée : %s", exc)
                    yield step_finished("fiscalonline", time.time() - t0, status="error",
                                        error=str(exc))

            # ── 11. Rédactionnel ─────────────────────────────────────────────
            _checkpoint("redaction")
            yield step_started("redaction", docs=len(doc_enriched))
            yield SourcesEvent(sources=public_sources(doc_fiscalonline + ranked_keep))
            t0 = time.time()

            if stream_redaction:
                for chunk in agent_redactionnel_stream(
                    question, result_analyste, doc_enriched, google_key,
                    model_name=models["redactionnel"],
                ):
                    _checkpoint("redaction")     # annulation en ~1 fragment
                    delta = normalizer.feed(chunk)
                    if delta:
                        yield TextDelta(delta)
                answer_text, points_cles = normalizer.finish()
                raw_answer = normalizer.raw
                reponse = lire_json_beton(raw_answer)
            else:
                raw_answer = agent_redactionnel(
                    question, result_analyste, doc_enriched, google_key,
                    model_name=models["redactionnel"],
                )
                reponse = lire_json_beton(raw_answer)
                answer_text = (reponse.get("reponse_redigee") or reponse.get("reponse") or "").strip()
                points_cles = reponse.get("points_cles", [])
                if not answer_text:
                    # JSON illisible OU champ vide : on récupère le texte brut nettoyé
                    # plutôt que de noter une réponse vide (parse raté ≠ mauvaise réponse).
                    answer_text = clean_json_codefence(raw_answer or "").strip()
                    logger.warning(
                        "Redactionnel — 'reponse_redigee' vide/illisible (raw=%d chars), "
                        "repli texte brut. Aperçu: %r",
                        len(raw_answer or ""), (raw_answer or "")[:200],
                    )
            timings["redactionnel"] = time.time() - t0
            yield step_finished("redaction", timings["redactionnel"], chars=len(answer_text))

            logger.info("PIPELINE TERMINE en %.1fs — %d sources, %.4f USD",
                        time.time() - t_total, len(ranked_keep), ctx.total_cost)

            finalize_trace(output=answer_text)
            finalized = True
            yield ResultEvent(PipelineResult(
                question=question,
                answer_text=answer_text,
                points_cles=points_cles,
                analyste=analyst_json,
                sources=public_sources(doc_fiscalonline + ranked_keep, snippet_max=1000),
                scraped_context=[d.get("content", "") for d in doc_enriched if d.get("content")],
                selected_agents=selected_agents,
                config_name=config_name,
                trace_id=ctx.trace_id,
                models_config=models,
                total_cost_usd=ctx.total_cost,
                total_input_tokens=ctx.total_input_tokens,
                total_output_tokens=ctx.total_output_tokens,
                wall_clock_s=time.time() - t_total,
                cost_by_agent=ctx.cost_by_agent(),
                timings=timings,
                n_sources_raw=len(structured_results),
                n_sources_kept=len(ranked_keep),
                raw_response=reponse if isinstance(reponse, dict) else {},
                fiscalonline_count=len(doc_fiscalonline),
            ))

        except (PipelineCancelled, PipelineDeadlineExceeded) as exc:
            logger.info("Pipeline interrompu : %s", exc)
            partial = normalizer.raw
            finalize_trace(output=partial or None,
                           metadata={"cancelled": True, "step": exc.step,
                                     "type": type(exc).__name__})
            finalized = True
            raise

        except GeneratorExit:
            # Le consommateur a fermé le générateur (déconnexion HTTP).
            # Interdit de `yield` ici : Python lèverait « generator ignored GeneratorExit ».
            logger.info("Pipeline fermé par le consommateur (déconnexion)")
            finalize_trace(output=normalizer.raw or None,
                           metadata={"cancelled": True, "reason": "generator_exit"})
            finalized = True
            raise

        except Exception as exc:
            logger.exception("run_pipeline_stream — échec sur la question : %r", question[:80])
            finalize_trace(metadata={"error": f"{type(exc).__name__}: {exc}"})
            finalized = True
            yield ResultEvent(PipelineResult(
                question=question, answer_text="", points_cles=[], analyste=analyst_json,
                sources=[], scraped_context=[], selected_agents=selected_agents,
                config_name=config_name, trace_id=ctx.trace_id, models_config=models,
                total_cost_usd=ctx.total_cost,
                total_input_tokens=ctx.total_input_tokens,
                total_output_tokens=ctx.total_output_tokens,
                wall_clock_s=time.time() - t_total,
                cost_by_agent=ctx.cost_by_agent(), timings=timings,
                error=f"{type(exc).__name__}: {exc}",
            ))

        finally:
            if fisca_executor is not None:
                fisca_executor.shutdown(wait=False, cancel_futures=True)
            if not finalized:
                finalize_trace(metadata={"incomplete": True})


# ─── Point d'entrée bloquant (éval, CLI) ──────────────────────────────────────
def run_pipeline(
    question: str,
    models_config: Optional[Dict[str, str]] = None,
    use_justicelibre: bool = True,
    active_domains: Optional[List[str]] = None,
    config_name: Optional[str] = None,
    *,
    use_fiscalonline: Optional[bool] = None,
    trace: Optional[TraceOptions] = None,
    deadline_s: Optional[float] = None,
) -> PipelineResult:
    """Exécute le pipeline complet et retourne le `PipelineResult`.

    Draine `run_pipeline_stream(stream_redaction=False)` : le rédactionnel est
    appelé en mode bloquant `json_mode=True`, comme historiquement, pour que le
    chemin d'évaluation reste inchangé.
    """
    result: Optional[PipelineResult] = None
    try:
        for event in run_pipeline_stream(
            question,
            models_config=models_config,
            active_domains=active_domains,
            use_justicelibre=use_justicelibre,
            use_fiscalonline=use_fiscalonline,
            stream_redaction=False,
            config_name=config_name,
            trace=trace,
            deadline_s=deadline_s,
        ):
            if isinstance(event, ResultEvent):
                result = event.result
    except (PipelineCancelled, PipelineDeadlineExceeded) as exc:
        return PipelineResult(
            question=question, answer_text="", points_cles=[], analyste={},
            sources=[], scraped_context=[], selected_agents=[],
            config_name=config_name, models_config={**DEFAULT_MODELS, **(models_config or {})},
            cancelled=True, error=f"{type(exc).__name__}: {exc}",
        )

    if result is None:  # pragma: no cover — le générateur rend toujours un ResultEvent
        result = PipelineResult(
            question=question, answer_text="", points_cles=[], analyste={},
            sources=[], scraped_context=[], selected_agents=[],
            config_name=config_name, models_config={**DEFAULT_MODELS, **(models_config or {})},
            error="Le pipeline n'a produit aucun résultat.",
        )
    return result


# ─── Utilitaires ──────────────────────────────────────────────────────────────
def _parse_dork_queries(raw: Any) -> List[str]:
    """Parse la sortie de l'agent jurisprudence (liste Python, éventuellement fencée).

    `clean_json_codefence` d'abord : Claude enveloppe sa liste dans ```python …```,
    ce qui faisait échouer `ast.literal_eval` — et l'échec était avalé
    silencieusement, produisant zéro requête de jurisprudence.
    """
    try:
        if isinstance(raw, str):
            raw = clean_json_codefence(raw)
            parsed = ast.literal_eval(raw)
        else:
            parsed = raw
        return parsed if isinstance(parsed, list) else []
    except Exception:
        logger.warning("Jurisprudence dork — sortie non parsable, 0 requête")
        return []
