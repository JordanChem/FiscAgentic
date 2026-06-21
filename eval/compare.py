"""
Comparaison de configurations de modèles : qualité × coût × latence (objectif 2).

Pour chaque config (eval/configs.py) × chaque question du golden set :
  - exécute le pipeline (avec cache) ;
  - mesure la couverture d'articles (DÉTERMINISTE, gratuite — toujours) ;
  - mesure la couverture des éléments attendus (GEval, LLM-juge — option --quality-judge) ;
  - relève coût et latence (capturés par la couche utils.llm).
Sort une table comparative (console + CSV), triable.

Tracing : chaque exécution est déjà tracée dans Langfuse (callback LiteLLM, taggée par
nom de config) → vue côte-à-côte coût/latence native. En complément, si les clés
Langfuse sont présentes, les scores qualité sont attachés aux traces (best-effort).

Usage :
    python -m eval.compare --dataset golden.csv --configs baseline gemini3-pro claude
    python -m eval.compare --dataset golden.csv --configs baseline claude --quality-judge gpt-4o --limit 10
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
for _lib in ("urllib3", "httpx", "httpcore", "LiteLLM", "google", "openai"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

from eval.dataset import load_golden, stratified_sample
from eval.configs import get_config, CONFIGS
from eval.cache import run_pipeline_cached
from eval.metrics import article_coverage, make_element_coverage_metric
from eval.run_eval import build_test_case, _source_blobs


def _maybe_langfuse_score(trace_id, name, value, comment=""):
    """Attache un score à une trace Langfuse (best-effort, ignore si non configuré)."""
    if not (trace_id and os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return
    try:
        from langfuse import Langfuse
        Langfuse().score(trace_id=trace_id, name=name, value=value, comment=comment)
    except Exception as exc:
        logging.debug("Langfuse score non envoyé : %s", exc)


def evaluate_config(config_name: str, cases, use_jl: bool, judge_model: str = None, force=False):
    """Exécute le pipeline + mesures pour une config sur tout le golden set."""
    models = get_config(config_name)
    element_metric = make_element_coverage_metric(judge_model) if judge_model else None

    art_recalls, elem_scores, costs, latencies = [], [], [], []
    for c in cases:
        result = run_pipeline_cached(c.question, models, use_justicelibre=use_jl,
                                     config_name=config_name, force=force)
        # Couverture d'articles sur les SOURCES conservées (juge LLM si fourni)
        art = (article_coverage(c.question, c.expected_articles, _source_blobs(result),
                                judge_model=judge_model)["recall"]
               if c.expected_articles else None)
        if art is not None:
            art_recalls.append(art)
            _maybe_langfuse_score(result.trace_id, "article_coverage", art)

        # Couverture des éléments (GEval, optionnel)
        if element_metric and c.expected_elements:
            try:
                tc = build_test_case(c, result)
                element_metric.measure(tc)
                elem_scores.append(element_metric.score)
                _maybe_langfuse_score(result.trace_id, "element_coverage", element_metric.score)
            except Exception as exc:
                logging.warning("GEval échec (cas %s) : %s", c.id, exc)

        costs.append(result.total_cost_usd)
        latencies.append(result.wall_clock_s)

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "config": config_name,
        "n": len(cases),
        "article_recall": _mean(art_recalls),
        "element_coverage": _mean(elem_scores) if elem_scores else None,
        "cost_total": sum(costs),
        "cost_mean": _mean(costs),
        "latency_mean": _mean(latencies),
    }


def print_table(rows):
    headers = ["config", "n", "article_recall", "element_cov", "cost_total($)", "cost/q($)", "latence_moy(s)"]
    print("\n" + "  ".join(f"{h:>14}" for h in headers))
    print("  ".join("-" * 14 for _ in headers))
    for r in rows:
        elem = f"{r['element_coverage']:.2f}" if r["element_coverage"] is not None else "—"
        print("  ".join([
            f"{r['config']:>14}", f"{r['n']:>14}", f"{r['article_recall']:>14.2f}",
            f"{elem:>14}", f"{r['cost_total']:>14.4f}", f"{r['cost_mean']:>14.5f}",
            f"{r['latency_mean']:>14.1f}",
        ]))


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nTable écrite : {path}")


def main():
    ap = argparse.ArgumentParser(description="Comparaison de configs de modèles (qualité × coût × latence).")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--configs", nargs="+", default=["baseline"], help=f"Configs à comparer parmi {list(CONFIGS)}")
    ap.add_argument("--quality-judge", default=None, help="Modèle-juge pour la couverture des éléments (sinon ignorée).")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", nargs="+", default=None, help="Ne traiter que ces ids de questions (ex. q3 q7).")
    ap.add_argument("--sample-stratified", type=int, default=0,
                    help="Échantillon de N questions équilibré par difficulté (déterministe).")
    ap.add_argument("--no-jl", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", default="eval/comparison.csv")
    args = ap.parse_args()

    cases = load_golden(args.dataset)
    if args.ids:
        wanted = set(args.ids)
        cases = [c for c in cases if c.id in wanted]
    if args.sample_stratified:
        cases = stratified_sample(cases, args.sample_stratified)
    if args.limit:
        cases = cases[:args.limit]
    print(f"Comparaison sur {len(cases)} questions | configs : {', '.join(args.configs)}")

    rows = []
    for cfg in args.configs:
        logging.info("=== Config : %s ===", cfg)
        rows.append(evaluate_config(cfg, cases, use_jl=not args.no_jl,
                                    judge_model=args.quality_judge, force=args.force))

    # Tri : meilleure qualité d'articles d'abord, puis coût croissant.
    rows.sort(key=lambda r: (-r["article_recall"], r["cost_mean"]))
    print_table(rows)
    write_csv(rows, args.out)


if __name__ == "__main__":
    main()
