"""
Test bout-en-bout du pipeline Fisca sans Streamlit (CLI mince).

La logique du pipeline vit désormais dans `pipeline/core.py` (fonction pure
`run_pipeline` qui RETOURNE un PipelineResult). Ce script ne fait que l'appeler
et afficher le résultat + les métriques coût/latence.

Usage :
    python test_pipeline.py
    python test_pipeline.py "Quelles sont les conditions d'exonération de TVA ... ?"
    python test_pipeline.py "..." --no-jl     # force SerpAPI seul (sans JusticeLibre)
    python test_pipeline.py "..." --stream    # consomme le générateur d'événements
                                              # (même chemin que l'API SSE)
    python test_pipeline.py "..." --config baseline   # config de modèles nommée
"""
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
for _lib in ("urllib3", "httpcore", "httpx", "google", "openai", "h11", "LiteLLM"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

from pipeline.core import run_pipeline, run_pipeline_stream
from pipeline.events import ResultEvent, SourcesEvent, StepEvent, TextDelta


def _sep(label=""):
    width = 70
    if label:
        pad = (width - len(label) - 2) // 2
        print(f"\n{'─' * pad} {label} {'─' * pad}")
    else:
        print("─" * width)


def _run_streaming(question, use_jl, models_config):
    """Consomme le générateur d'événements — exactement ce que fait l'API SSE."""
    result = None
    for event in run_pipeline_stream(question, use_justicelibre=use_jl,
                                     models_config=models_config):
        if isinstance(event, StepEvent):
            if event.status == "running":
                print(f"\n[{event.progress:3d}%] {event.label}…", flush=True)
            else:
                extra = " ".join(f"{k}={v}" for k, v in event.meta.items())
                print(f"        ↳ {event.status} en {event.elapsed_s}s  {extra}", flush=True)
        elif isinstance(event, SourcesEvent):
            print(f"\n>>> {len(event.sources)} sources retenues", flush=True)
        elif isinstance(event, TextDelta):
            print(event.delta, end="", flush=True)
        elif isinstance(event, ResultEvent):
            result = event.result
    print()
    return result


def main():
    argv = sys.argv[1:]
    flags = [a for a in argv if a.startswith("--")]

    config_name = None
    if "--config" in argv:
        i = argv.index("--config")
        config_name = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]          # retire le flag ET sa valeur

    args = [a for a in argv if not a.startswith("--")]
    question = args[0] if args else (
        "Quelles sont les conditions pour qu'un tribunal administratif annule "
        "un redressement fiscal en matière de TVA ?"
    )
    use_jl = "--no-jl" not in flags

    models_config = None
    if config_name:
        from eval.configs import get_config
        models_config = get_config(config_name)

    _sep("QUESTION")
    print(question)

    if "--stream" in flags:
        _sep("FLUX")
        res = _run_streaming(question, use_jl, models_config)
    else:
        res = run_pipeline(question, models_config, use_justicelibre=use_jl)

    if res.error:
        _sep("ERREUR")
        print(res.error)
        return

    _sep("AGENTS SÉLECTIONNÉS")
    print(", ".join(res.selected_agents) or "(aucun)")

    _sep(f"SOURCES RETENUES ({res.n_sources_kept}/{res.n_sources_raw})")
    for r in res.sources[:10]:
        print(f"  [{r.get('score', 0):.2f}]  {r.get('source_domain', ''):<35}  {r.get('title', '')[:55]}")

    _sep("RÉPONSE FINALE")
    print(res.answer_text or "(vide)")

    if res.points_cles:
        _sep("Points clés")
        for pt in res.points_cles:
            print(f"  • {pt}")

    _sep("MÉTRIQUES")
    print(f"Latence totale : {res.wall_clock_s:.1f}s")
    print(f"Coût total     : ${res.total_cost_usd:.5f}  "
          f"(in={res.total_input_tokens} / out={res.total_output_tokens} tokens)")
    print("Coût par agent :")
    for agent, cost in sorted(res.cost_by_agent.items(), key=lambda x: -x[1]):
        t = res.timings.get(agent)
        t_str = f"  ({t:.1f}s)" if t else ""
        print(f"   ${cost:.5f}  {agent}{t_str}")
    print("Latence par étape :")
    for step, secs in res.timings.items():
        print(f"   {secs:5.1f}s  {step}")


if __name__ == "__main__":
    main()
