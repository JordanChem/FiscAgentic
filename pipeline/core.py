"""
Pipeline fiscal en **fonction pure** : `run_pipeline(question, models_config) -> PipelineResult`.

Contrairement à l'ancien `test_pipeline.run_pipeline` (qui ne faisait que `print`),
cette version :
- **retourne** un objet structuré (réponse, sources, contexte scrapé, métriques) ;
- accepte un `models_config` (override du modèle par agent) → permet de comparer
  des configurations de modèles (objectif 2) ;
- enveloppe toute l'exécution dans une **trace LLM** (`utils.llm.llm_trace`) qui
  agrège coût / tokens / latence par agent (et trace Langfuse si configuré).

Le flux séquentiel reproduit fidèlement celui de `test_pipeline.py` (chemin
non-Streamlit éprouvé). [test_pipeline.py](../test_pipeline.py) devient un mince CLI
qui appelle cette fonction et affiche le résultat.
"""
from __future__ import annotations

import ast
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
from agents.redactionnel import agent_redactionnel
from utils.json_utils import lire_json_beton, clean_json_codefence
from utils.search import search_with_fallback, OFFICIAL_DOMAINS
from utils.scraper_utils import scrapper
from utils.llm import llm_trace, trace_step, finalize_trace
from utils.api_keys import get_api_keys

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

# Configuration de modèles par défaut (nom logique par agent).
DEFAULT_MODELS: Dict[str, str] = {
    "analyste":      "gemini-2.5-flash",
    "generaliste":   "gpt-4o",
    "jurisprudence": "gemini-2.5-flash",
    "orchestrateur": "gpt-4o",
    "ranker":        "gpt-4o",
    "redactionnel":  "gemini-2.5-flash",
    "specialises":   "gemini-2.5-flash",
    "verificateur":  "gemini-2.5-flash",
}


@dataclass
class PipelineResult:
    """Sortie structurée d'une exécution du pipeline pour une question."""
    question: str
    answer_text: str
    points_cles: List[str]
    analyste: dict
    sources: List[dict]            # sources retenues (ranked keep) enrichies
    scraped_context: List[str]     # contenus scrapés (retrieval_context faithfulness)
    selected_agents: List[str]
    # ── Métriques (objectif 2 : comparaison qualité × coût × latence) ──
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


def run_pipeline(
    question: str,
    models_config: Optional[Dict[str, str]] = None,
    use_justicelibre: bool = True,
    active_domains: Optional[List[str]] = None,
    config_name: Optional[str] = None,
) -> PipelineResult:
    """Exécute le pipeline complet pour une question et retourne un PipelineResult.

    Args:
        question: la question fiscale.
        models_config: override {agent: nom_logique}. Fusionné avec DEFAULT_MODELS.
        use_justicelibre: active la recherche JusticeLibre (CE/Cass/CJUE).
        active_domains: domaines officiels autorisés (défaut = OFFICIAL_DOMAINS).
        config_name: étiquette de la config (pour le tracing/la comparaison).
    """
    models = {**DEFAULT_MODELS, **(models_config or {})}
    active_domains = active_domains or OFFICIAL_DOMAINS.copy()
    openai_key, google_key, serpapi_key = get_api_keys()

    timings: Dict[str, float] = {}
    t_total = time.time()

    with llm_trace(name="fisca-question", input=question, config_name=config_name,
                   tags=["pipeline", config_name or "default"]) as ctx:
        try:
            # 1. Analyste
            t0 = time.time()
            result_analyste = agent_analyste(question, google_key, model_name=models["analyste"])
            analyst_json = lire_json_beton(result_analyste)
            timings["analyste"] = time.time() - t0

            # 2. Orchestrateur
            t0 = time.time()
            routing = lire_json_beton(
                agent_orchestrateur(question, result_analyste, openai_key, model_name=models["orchestrateur"])
            )
            selected_agents = routing.get("selected_agents", [])
            timings["orchestrateur"] = time.time() - t0
            trace_step("routage", output={"selected_agents": selected_agents,
                                          "scores": routing.get("scores", {})})

            if not selected_agents:
                finalize_trace(output="Aucun agent sélectionné — question hors périmètre fiscal.")
                return _empty_result(
                    question, models, config_name, ctx, timings, t_total, analyst_json,
                    answer="Aucun agent sélectionné — question hors périmètre fiscal.",
                )

            # 3. Agents spécialisés (parallèle)
            t0 = time.time()
            valid_agents = [n for n in selected_agents if n in AGENT_FUNCTIONS]
            results: Dict[str, str] = {}

            def _call(name):
                fn = AGENT_FUNCTIONS[name]
                return name, fn(question, result_analyste, google_key,
                                available_domain=active_domains,
                                model_name=models["specialises"])

            # copy_context().run propage la trace LLM aux workers (sinon générations
            # spécialistes orphelines + coûts non agrégés dans le RunContext).
            # IMPORTANT : copy_context() doit être évalué ICI (thread principal) — via
            # submit, pas dans un lambda passé à map() qui s'exécuterait côté worker.
            with ThreadPoolExecutor(max_workers=max(1, len(valid_agents))) as ex:
                futures = [ex.submit(copy_context().run, _call, n) for n in valid_agents]
                for fut in futures:
                    name, res = fut.result()
                    if res:
                        results[name] = res
            timings["specialises"] = time.time() - t0
            trace_step("specialistes", output={n: results.get(n) for n in valid_agents},
                       metadata={"repondants": list(results.keys()), "demandes": valid_agents})

            # 4. Vérificateur
            t0 = time.time()
            verified_sources = lire_json_beton(
                agent_verificateur(question, result_analyste, results, google_key, model_name=models["verificateur"])
            )
            timings["verificateur"] = time.time() - t0
            trace_step("verification", output=verified_sources)

            # 5. Généraliste
            t0 = time.time()
            queries = agent_generaliste(question, openai_key, active_domains=active_domains, model_name=models["generaliste"])
            timings["generaliste"] = time.time() - t0
            trace_step("requetes_generaliste", output=queries, metadata={"n_requetes": len(queries)})

            # 5b. Jurisprudence dork
            t0 = time.time()
            try:
                raw = generate_jurisprudence_dork(question, result_analyste, google_key, model_name=models["jurisprudence"])
                if isinstance(raw, str):
                    raw = clean_json_codefence(raw)  # Claude enveloppe en ```python …```
                jurisprudence_queries = ast.literal_eval(raw) if isinstance(raw, str) else raw
                if not isinstance(jurisprudence_queries, list):
                    jurisprudence_queries = []
            except Exception:
                jurisprudence_queries = []
            timings["jurisprudence"] = time.time() - t0

            # 6. Concaténation des requêtes
            l_experts = [v for lst in verified_sources.values() if isinstance(lst, list) for v in lst]
            full_queries = queries + l_experts + jurisprudence_queries

            # 7. Recherche (non-LLM)
            t0 = time.time()
            structured_results = search_with_fallback(
                full_queries, serpapi_key,
                active_domains=active_domains,
                use_justicelibre=use_justicelibre,
                analyst_json=analyst_json,
            )
            timings["search"] = time.time() - t0
            trace_step("recherche", metadata={"n_requetes": len(full_queries),
                                              "n_resultats_bruts": len(structured_results)})

            # 8. Déduplication
            seen, unique = set(), []
            for r in structured_results:
                url = r.get("url")
                if url and url not in seen:
                    unique.append(r)
                    seen.add(url)
            trace_step("deduplication", metadata={"avant": len(structured_results),
                                                  "apres": len(unique)})

            # 9. Ranker
            t0 = time.time()
            ranked = agent_ranker(question, unique, result_analyste, results, openai_key, model=models["ranker"])
            ranked_keep = [x for x in ranked if x.get("keep") and x.get("score", 0) >= 0.8]
            if not ranked_keep:
                ranked_keep = [x for x in ranked if x.get("keep") and x.get("score", 0) >= 0.6]
            timings["ranker"] = time.time() - t0
            trace_step(
                "ranking",
                output=[{"url": x.get("url"), "score": x.get("score"), "reason": x.get("reason")} for x in ranked_keep],
                metadata={"candidats": len(unique), "retenues": len(ranked_keep)},
            )

            # 10. Scraping (non-LLM)
            t0 = time.time()
            doc_enriched = scrapper(ranked_keep)
            timings["scraping"] = time.time() - t0
            trace_step("scraping", metadata={
                "urls_avec_contenu": sum(1 for d in doc_enriched if d.get("content")),
                "urls_total": len(doc_enriched),
            })

            # 11. Rédactionnel (non-streamé : on a besoin du texte complet pour la notation)
            t0 = time.time()
            reponse_raw = agent_redactionnel(question, result_analyste, doc_enriched,
                                             google_key, model_name=models["redactionnel"])
            reponse = lire_json_beton(reponse_raw)
            timings["redactionnel"] = time.time() - t0

            answer_text = reponse.get("reponse_redigee") or reponse.get("reponse") or ""
            if not answer_text:
                # JSON illisible OU champ vide : on récupère le texte brut nettoyé plutôt
                # que de noter une réponse vide (parse raté ≠ mauvaise réponse).
                answer_text = clean_json_codefence(reponse_raw or "").strip()
                logger.warning(
                    "Redactionnel — 'reponse_redigee' vide/illisible (raw=%d chars), "
                    "fallback texte brut. Aperçu: %r",
                    len(reponse_raw or ""), (reponse_raw or "")[:200],
                )
            scraped_context = [d.get("content", "") for d in doc_enriched if d.get("content")]

            finalize_trace(output=answer_text)
            return PipelineResult(
                question=question,
                answer_text=answer_text,
                points_cles=reponse.get("points_cles", []),
                analyste=analyst_json,
                sources=ranked_keep,
                scraped_context=scraped_context,
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
                raw_response=reponse,
            )

        except Exception as exc:
            logger.exception("run_pipeline — échec sur la question : %r", question[:80])
            finalize_trace(metadata={"error": f"{type(exc).__name__}: {exc}"})
            return PipelineResult(
                question=question, answer_text="", points_cles=[], analyste={},
                sources=[], scraped_context=[], selected_agents=[],
                config_name=config_name, trace_id=ctx.trace_id, models_config=models,
                total_cost_usd=ctx.total_cost,
                total_input_tokens=ctx.total_input_tokens,
                total_output_tokens=ctx.total_output_tokens,
                wall_clock_s=time.time() - t_total,
                cost_by_agent=ctx.cost_by_agent(), timings=timings,
                error=f"{type(exc).__name__}: {exc}",
            )


def _empty_result(question, models, config_name, ctx, timings, t_total, analyst_json, answer):
    return PipelineResult(
        question=question, answer_text=answer, points_cles=[], analyste=analyst_json,
        sources=[], scraped_context=[], selected_agents=[],
        config_name=config_name, trace_id=ctx.trace_id, models_config=models,
        total_cost_usd=ctx.total_cost,
        total_input_tokens=ctx.total_input_tokens,
        total_output_tokens=ctx.total_output_tokens,
        wall_clock_s=time.time() - t_total,
        cost_by_agent=ctx.cost_by_agent(), timings=timings,
    )
