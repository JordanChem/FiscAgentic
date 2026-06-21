"""
Harnais de notation du golden dataset (deepeval).

Pour chaque question du golden set :
  1. exécute le pipeline (avec cache) sous la config de modèles choisie ;
  2. construit un LLMTestCase (réponse + sources scrapées + attendus) ;
  3. applique les métriques : couverture d'articles (déterministe) + couverture des
     éléments attendus (GEval) + fidélité aux sources (Faithfulness).
Le rapport deepeval s'affiche en console et, si vous êtes connecté à Confident AI
(`deepeval login`), il est poussé sur le dashboard cloud.

Usage :
    python -m eval.run_eval --dataset chemin/vers/golden.csv
    python -m eval.run_eval --dataset golden.xlsx --config gemini3-pro --judge gpt-4o --limit 5
"""
from __future__ import annotations

import argparse
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
for _lib in ("urllib3", "httpx", "httpcore", "LiteLLM", "google", "openai"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

from deepeval import evaluate
from deepeval.evaluate.configs import ErrorConfig
from deepeval.test_case import LLMTestCase

from eval.dataset import load_golden, stratified_sample
from eval.configs import get_config
from eval.cache import run_pipeline_cached
from eval.metrics import (
    ArticleCoverageMetric, make_element_coverage_metric, make_faithfulness_metric,
)


# Bornes du contexte envoyé aux juges LLM (faithfulness) : un doc scrapé peut peser
# plusieurs Mo → sans cap on dépasse le contexte du juge (128k tokens pour gpt-4o).
_MAX_CTX_PER_DOC = 8000     # chars par document
_MAX_CTX_TOTAL = 80000      # chars cumulés (~20k tokens, marge sous 128k)


def _truncate_context(chunks: list) -> list:
    """Tronque chaque doc et plafonne le total pour rester sous le contexte du juge."""
    out, total = [], 0
    for ch in chunks:
        ch = (ch or "")[:_MAX_CTX_PER_DOC]
        if total + len(ch) > _MAX_CTX_TOTAL:
            ch = ch[: max(0, _MAX_CTX_TOTAL - total)]
        if ch:
            out.append(ch)
            total += len(ch)
        if total >= _MAX_CTX_TOTAL:
            break
    return out


def _source_blobs(result) -> list:
    """Sources conservées après ranking, en **titre + URL uniquement** — c'est la liste
    que le juge des articles examine. On exclut l'extrait (snippet SerpAPI), tronqué et
    trompeur : le titre + l'URL identifient bien plus proprement l'article/la source."""
    blobs = []
    for s in result.sources:
        blobs.append(f"{s.get('title','')} | {s.get('url','')}")
    return blobs


def build_test_case(case, result) -> LLMTestCase:
    return LLMTestCase(
        input=case.question,
        actual_output=result.answer_text or "(réponse vide)",
        expected_output="\n- " + "\n- ".join(case.expected_elements) if case.expected_elements else "",
        retrieval_context=_truncate_context(result.scraped_context) or [result.answer_text or ""],
        additional_metadata={
            "id": case.id,
            "domaine": case.domaine,
            "difficulte": case.difficulte,
            "expected_articles": case.expected_articles,
            # Liste des articles/sources conservés après ranking (titre + URL) — visible
            # sur le dashboard ET lue par la métrique de couverture des articles.
            "kept_articles": _source_blobs(result),
            "cost_usd": result.total_cost_usd,
            "latency_s": result.wall_clock_s,
        },
    )


def _pct(score):
    return round(100 * score, 1) if isinstance(score, (int, float)) else None


def _write_per_question_csv(eval_result, path: str) -> None:
    """Écrit un CSV avec, PAR QUESTION : coût, temps, % éléments, % articles, faithfulness."""
    import csv
    rows = []
    for tr in getattr(eval_result, "test_results", []) or []:
        meta = tr.additional_metadata or {}
        scores = {md.name: md.score for md in (tr.metrics_data or [])}
        def _find(substr):
            for name, sc in scores.items():
                if substr in name.lower():
                    return sc
            return None
        rows.append({
            "id": meta.get("id"),
            "difficulte": meta.get("difficulte"),
            "domaine": meta.get("domaine"),
            "cout_usd": round(meta.get("cost_usd", 0) or 0, 5),
            "temps_s": round(meta.get("latency_s", 0) or 0, 1),
            "elements_pct": _pct(_find("éléments")),
            "articles_pct": _pct(_find("articles")),
            "faithfulness_pct": _pct(_find("faithful")),
        })
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n📄 Métriques par question écrites : {path}")


def main():
    ap = argparse.ArgumentParser(description="Notation du golden dataset (deepeval).")
    ap.add_argument("--dataset", required=True, help="Chemin du fichier golden (csv/xlsx/json/jsonl).")
    ap.add_argument("--config", default="baseline", help="Nom de la config de modèles (eval/configs.py).")
    ap.add_argument("--judge", default="gpt-4o", help="Modèle-juge pour GEval/Faithfulness.")
    ap.add_argument("--limit", type=int, default=0, help="Limiter au N premiers cas (0 = tous).")
    ap.add_argument("--ids", nargs="+", default=None, help="Ne traiter que ces ids de questions (ex. q3 q7).")
    ap.add_argument("--sample-stratified", type=int, default=0,
                    help="Échantillon de N questions équilibré par difficulté (déterministe).")
    ap.add_argument("--no-jl", action="store_true", help="Désactive JusticeLibre.")
    ap.add_argument("--no-faithfulness", action="store_true", help="Désactive la métrique de fidélité.")
    ap.add_argument("--article-regex-only", action="store_true",
                    help="Couverture d'articles en regex pur (sans juge LLM sémantique).")
    ap.add_argument("--force", action="store_true", help="Ignore le cache pipeline.")
    args = ap.parse_args()

    cases = load_golden(args.dataset)
    if args.ids:
        wanted = set(args.ids)
        cases = [c for c in cases if c.id in wanted]
    if args.sample_stratified:
        cases = stratified_sample(cases, args.sample_stratified)
    if args.limit:
        cases = cases[:args.limit]
    print(f"Golden dataset : {len(cases)} cas | config={args.config} | juge={args.judge}")

    models = get_config(args.config)
    article_judge = None if args.article_regex_only else args.judge
    metrics = [
        ArticleCoverageMetric(threshold=0.7, judge_model=article_judge),
        make_element_coverage_metric(args.judge, threshold=0.7),
    ]
    if not args.no_faithfulness:
        metrics.append(make_faithfulness_metric(args.judge, threshold=0.7))

    test_cases = []
    total_cost = total_latency = 0.0
    for c in cases:
        if c.is_follow_up:
            logging.info("Cas %s marqué follow-up — exécuté en question autonome.", c.id)
        result = run_pipeline_cached(c.question, models, use_justicelibre=not args.no_jl,
                                     config_name=args.config, force=args.force)
        if result.error:
            logging.warning("Cas %s — pipeline en erreur : %s", c.id, result.error)
        total_cost += result.total_cost_usd
        total_latency += result.wall_clock_s
        test_cases.append(build_test_case(c, result))

    print(f"\nPipeline — coût total ${total_cost:.4f} | latence cumulée {total_latency:.0f}s "
          f"(moy {total_latency/max(1,len(cases)):.1f}s/question)\n")

    # Hyperparamètres du run : décrivent la config testée → permettent de comparer
    # proprement deux runs dans Confident AI (quel modèle par agent, juge, options).
    hyperparameters = {
        "config": args.config,
        "judge": args.judge,
        "justicelibre": str(not args.no_jl),
        "article_matching": "regex" if args.article_regex_only else f"hybride+{args.judge}",
        "cout_pipeline_usd": round(total_cost, 4),
    }
    for agent, model in models.items():
        hyperparameters[f"model_{agent}"] = model

    # Lance la notation deepeval (affichage console + push Confident AI si connecté).
    # `identifier` = nom lisible du run dans le dashboard.
    # `ignore_errors=True` : si une métrique échoue sur un cas (ex. contexte trop long),
    # le cas est marqué en erreur mais le run se termine et s'uploade quand même.
    try:
        eval_result = evaluate(
            test_cases=test_cases,
            metrics=metrics,
            hyperparameters=hyperparameters,
            identifier=f"{args.config} (n={len(cases)})",
            error_config=ErrorConfig(ignore_errors=True),
        )
        _write_per_question_csv(eval_result, f"eval/per_question_{args.config}.csv")
    except Exception as exc:
        # Ici l'échec est global (réseau, auth Confident AI…), pas une métrique isolée.
        logging.warning(
            "Notation locale affichée ci-dessus. Échec global de evaluate()/upload : %s — "
            "si c'est un problème de dashboard, vérifiez 'deepeval login' / CONFIDENT_API_KEY ; "
            "sinon relancez (le cache évite de refaire le pipeline).",
            exc,
        )


if __name__ == "__main__":
    main()
