"""
Test bout-en-bout du pipeline Fisca sans Streamlit (CLI mince).

La logique du pipeline vit désormais dans `pipeline/core.py` (fonction pure
`run_pipeline` qui RETOURNE un PipelineResult). Ce script ne fait que l'appeler
et afficher le résultat + les métriques coût/latence.

Usage :
    python test_pipeline.py
    python test_pipeline.py "Quelles sont les conditions d'exonération de TVA ... ?"
    python test_pipeline.py "..." --no-jl     # force SerpAPI seul (sans JusticeLibre)
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

from pipeline.core import run_pipeline


def _sep(label=""):
    width = 70
    if label:
        pad = (width - len(label) - 2) // 2
        print(f"\n{'─' * pad} {label} {'─' * pad}")
    else:
        print("─" * width)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    question = args[0] if args else (
        "Quelles sont les conditions pour qu'un tribunal administratif annule "
        "un redressement fiscal en matière de TVA ?"
    )
    use_jl = "--no-jl" not in flags

    _sep("QUESTION")
    print(question)

    res = run_pipeline(question, use_justicelibre=use_jl)

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
