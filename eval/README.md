# Évaluation du pipeline fiscal

Deux objectifs : **(1)** noter la qualité du pipeline sur un golden dataset, **(2)**
comparer des modèles/providers sur **qualité × coût × latence**.

## Architecture

```
utils/model_registry.py   nom logique de modèle → id LiteLLM (+ prix custom)
utils/llm.py              llm_call / llm_call_stream : appel unique LiteLLM,
                          capture coût/tokens/latence, tracing Langfuse, trace par question
pipeline/core.py          run_pipeline(question, models_config) -> PipelineResult (fonction pure)
eval/dataset.py           chargement du golden (CSV/XLSX/JSON/JSONL + mapping colonnes)
eval/articles.py          normalisation/matching des références d'articles (déterministe)
eval/metrics.py           ArticleCoverage (déterministe) + GEval éléments + Faithfulness
eval/cache.py             cache disque des PipelineResult (évite de relancer le pipeline)
eval/configs.py           configs de modèles nommées (baseline, gemini3-pro, claude…)
eval/run_eval.py          notation du golden set (deepeval → console + Confident AI)
eval/compare.py           comparaison multi-configs (table qualité×coût×latence + CSV)
```

## Prérequis

```bash
pip install -r requirements.txt
cp .env.example .env        # puis remplir les clés (OpenAI, Google, Anthropic, SerpAPI…)
# Optionnel : dashboards
deepeval login              # ou CONFIDENT_API_KEY dans .env  → dashboard qualité
# LANGFUSE_PUBLIC_KEY/SECRET_KEY dans .env → tracing coût/latence
```

## 1. Brancher le golden dataset

Le loader accepte CSV / Excel / JSON / JSONL. Schéma attendu (colonnes, noms flexibles) :
`question`, `elements_attendus`, `articles_attendus`, `domaine` (option), `id` (option).
Voir le modèle : [golden_dataset.example.csv](golden_dataset.example.csv).

Les listes (éléments / articles) peuvent être séparées par `;`, retour à la ligne, `|`…
Si vos colonnes ont d'autres noms, ajustez `DEFAULT_COLUMN_MAP` dans
[dataset.py](dataset.py).

## 2. Noter la qualité (objectif 1)

```bash
python -m eval.run_eval --dataset chemin/vers/votre_golden.csv
python -m eval.run_eval --dataset golden.xlsx --config baseline --judge gpt-4o --limit 5
```

**Sous-ensemble de questions** (sur `run_eval` et `compare`) :
- `--limit N` : les N premières (ordre du fichier, **non représentatif**) ;
- `--ids q3 q7 …` : questions précises ;
- `--sample-stratified N` : N questions **équilibrées par difficulté**, **déterministe**
  (mêmes questions à chaque run → indispensable pour comparer des modèles équitablement).
Voir [GUIDE.md](GUIDE.md) §3 bis.

Métriques :
- **Couverture des articles** (hybride) : recall des articles attendus présents dans la
  **liste des sources retournées** (titres + extraits + URLs), pas dans le texte de la
  réponse. Matching regex puis juge LLM sur les manquants (URLs Legifrance opaques,
  variantes BOFiP…). `--article-regex-only` pour rester en regex pur.
- **Couverture des éléments attendus** (GEval, LLM-juge).
- **Fidélité** (Faithfulness) : anti-hallucination vs sources scrapées (`--no-faithfulness` pour désactiver).

## 3. Comparer des modèles (objectif 2)

```bash
python -m eval.compare --dataset golden.csv --configs baseline gemini3-pro claude
# avec notation qualité par juge (plus coûteux) :
python -m eval.compare --dataset golden.csv --configs baseline claude --quality-judge gpt-4o
```

Sort une table **qualité × coût × latence** par config (console + `eval/comparison.csv`).
Configs disponibles dans [configs.py](configs.py) ; ajoutez-en librement.

## Notes

- **Cache** : les exécutions du pipeline sont mises en cache (`eval/.cache/`). Ré-évaluer
  ne relance pas le pipeline. `--force` pour recalculer ; `eval.cache.clear_cache()` pour vider.
- **Prix des modèles preview** : LiteLLM ne connaît pas les tarifs des modèles preview
  (`gemini-3-*`, `gpt-5.2`). Renseignez-les dans `CUSTOM_PRICING`
  ([utils/model_registry.py](../utils/model_registry.py)) sinon leur coût ressort à 0.
- **Juge provider-agnostique** : le juge LLM (`LiteLLMJudge`) passe par `llm_call`, donc
  vous pouvez juger avec Gemini, OpenAI ou Claude (`--judge claude-opus-4-8`).
```
