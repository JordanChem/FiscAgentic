# Guide d'utilisation — Testing & évaluation du pipeline

Guide pratique pas-à-pas. Deux usages : **vérifier/déboguer** une réponse (rapide, sans
notation) et **noter la qualité** (golden dataset + comparaison de modèles).

> Toutes les commandes se lancent depuis la racine du projet, venv activé
> (`source venv/bin/activate`). Le golden dataset par défaut : `../golden_dataset_3.xlsx`.

---

## 0. Prérequis (une fois)

```bash
pip install -r requirements.txt
cp .env.example .env          # déjà fait — clés OpenAI/Google/Anthropic/SerpAPI/Langfuse
deepeval login                # (optionnel) dashboard qualité Confident AI
```

Les **dashboards** sont optionnels : tout marche aussi en console / CSV.

---

## 1. Lancer UNE question, SANS évaluation

Pour juste voir la réponse du pipeline + le coût + la latence (aucune note de qualité) :

```bash
python test_pipeline.py "Je fais 25 km pour aller travailler, puis-je déduire mes frais réels ?"
python test_pipeline.py "..." --no-jl     # sans JusticeLibre (SerpAPI seul)
```

**Où voir les résultats ?** Directement dans la **console** :
- la réponse rédigée + les points clés ;
- les sources retenues (score + domaine) ;
- **MÉTRIQUES** : coût total, coût **par agent**, latence **par étape**.

Si les clés Langfuse sont définies, cette exécution apparaît **aussi** dans Langfuse
(trace = la question, avec chaque appel LLM, son coût et sa latence).

---

## 2. Lancer UNE question, AVEC évaluation (notation qualité)

La notation a besoin des **attendus** (éléments + articles) → elle se fait donc sur une
question **du golden dataset**. On cible une question précise avec `--ids` (ou `--limit 1`
pour la première) :

```bash
# noter UNE question précise (ids = q1..q50, ordre du fichier)
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --ids q1

# noter la première question
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --limit 1
```

**Où voir les résultats ?**
- **Console** : un bloc « Metrics Summary » par question avec les 3 scores (voir §6) et la
  raison de chaque note.
- **Confident AI** (si `deepeval login` fait) : dashboard cloud avec l'historique des runs,
  le détail par question, les scores par métrique. L'URL est affichée en fin de commande.

> Pour évaluer une question **qui n'est pas dans le golden** : ajoutez-la au fichier Excel
> (avec ses éléments/articles attendus), elle aura un nouvel id `qN`.

---

## 3. Lancer TOUT le golden dataset

```bash
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --judge gpt-4o
```

Options utiles :
- `--config gemini3-pro` : change les modèles (voir §5) ;
- `--judge claude-opus-4-8` : change le modèle-juge ;
- `--no-faithfulness` : enlève la métrique de fidélité (plus rapide / moins cher) ;
- `--limit 10` : ne traiter que les 10 premières (test rapide) ;
- `--force` : ignorer le cache (voir §7).

Ordre de grandeur : ~190 s et ~0,17 $ **par question** (config baseline) → compter
~1 h et ~8–9 $ pour les 50. Le **cache** évite de tout relancer ensuite.

---

## 3 bis. Lancer sur un sous-ensemble (échantillonnage)

Trois façons de ne PAS traiter les 50 questions. Disponibles sur `run_eval` **et** `compare`.

| Option | Effet | Quand l'utiliser |
|---|---|---|
| `--limit 20` | les **20 premières** lignes du fichier (q1→q20, ordre Excel) | test de rodage rapide |
| `--ids q3 q7 q12 …` | exactement les questions listées | cibler des cas précis |
| `--sample-stratified 20` | **20 questions équilibrées par difficulté**, déterministe | sample **représentatif** |

```bash
# échantillon représentatif de 20 questions (équilibré Faible/Moyenne/Difficile/New)
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --sample-stratified 20 --judge gpt-4o
```

⚠️ **`--limit 20` n'est PAS représentatif** : le fichier est rangé par blocs de difficulté
(les `Faible`/`Moyenne` d'abord), donc les 20 premières ne couvrent pas les cas `Difficile`/`New`.

✅ **`--sample-stratified 20`** répartit proportionnellement (ex. sur ce dataset :
4 Faible + 4 Moyenne + 4 Difficile + 8 New) et renvoie **toujours les mêmes 20 questions** —
c'est essentiel pour **comparer deux modèles sur le même sous-ensemble** (sinon la comparaison
serait faussée par un changement de questions). Tu peux donc faire :

```bash
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --sample-stratified 20 --config baseline
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --sample-stratified 20 --config gemini3-pro
# → les deux runs portent EXACTEMENT les mêmes 20 questions, comparables dans Confident AI
```

---

## 4. Y a-t-il un dashboard ? (3 niveaux)

| Outil | Ce qu'on y voit | Comment |
|---|---|---|
| **Console / CSV** | Scores, coût/latence, table comparative | Toujours dispo. `eval/comparison.csv` pour la compa. |
| **Confident AI** (deepeval) | **Qualité** : runs, scores par question & par métrique, historique, régressions | `deepeval login` une fois ; `run_eval` pousse automatiquement |
| **Langfuse** | **Coût / latence / tokens** par appel et par question, filtrable par config | clés `LANGFUSE_*` dans `.env` → [cloud.langfuse.com](https://cloud.langfuse.com), filtre par **tag** = nom de config |

En résumé : **Confident AI = qualité**, **Langfuse = coût/latence/traces**.

### Confident AI — procédure pas à pas

1. **Se connecter** (une seule fois) : `deepeval login` → ouvre le navigateur, crée/connecte
   le compte, copie la clé API, colle-la dans le terminal. *(Alternative : `CONFIDENT_API_KEY` dans `.env`.)*
2. **Test rapide** (valider le push avant un long run) :
   `python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --ids q1 q2 q3`
   → en fin de commande, l'**URL du run** s'affiche. Ouvre-la.
3. **Run complet de référence** (empêcher la veille du Mac sur ~1 h) :
   `caffeinate -i python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --judge gpt-4o`
4. **Dashboard** : taux de réussite par métrique, détail par question (score, **raison**,
   réponse, sources), et les **hyperparamètres** du run (config + modèle par agent + juge + coût).
5. **Changer les modèles et relancer** :
   `caffeinate -i python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --config gemini3-pro --judge gpt-4o`
6. **Comparer** : dans Confident AI, sélectionne les deux runs (ex. `baseline` vs `gemini3-pro`)
   → vue côte à côte, **amélioration/régression par question et par métrique**. Les
   hyperparamètres indiquent exactement quel modèle a changé.

> Chaque run est automatiquement étiqueté (`identifier` = config + nb de questions) et
> porte ses hyperparamètres (modèle par agent, juge, mode de matching d'articles, coût) :
> c'est ce qui rend la comparaison de runs lisible.

---

## 5. Changer les modèles et relancer (pour voir si ça s'améliore)

Il y a **4 endroits**, du plus ponctuel au plus structurel :

**a) Le plus simple — choisir une config existante au lancement :**
```bash
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --config gemini3-pro
```
Configs livrées (dans [configs.py](configs.py)) : `baseline`, `gemini3-flash`,
`gemini3-pro`, `gpt5`, `claude`, `all-claude`.

**b) Créer / modifier une config nommée** → éditez [eval/configs.py](configs.py) :
```python
CONFIGS["mon_test"] = {**DEFAULT_MODELS, "redactionnel": "claude-opus-4-8", "ranker": "gpt-5.2"}
```
puis `--config mon_test`. (Agents disponibles : `analyste, orchestrateur, specialises,
verificateur, generaliste, jurisprudence, ranker, redactionnel`.)

**c) Changer le défaut global par agent** → `DEFAULT_MODELS` dans
[pipeline/core.py](../pipeline/core.py). C'est ce qu'utilise aussi `test_pipeline.py`.

**d) Ajouter un nouveau modèle / provider** → `MODEL_REGISTRY` dans
[utils/model_registry.py](../utils/model_registry.py) (+ `CUSTOM_PRICING` pour le coût des
modèles preview, sinon coût = 0).

**Boucle d'amélioration typique :**
```bash
# 1. baseline sur une question
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --ids q12
# 2. on tente un autre modèle sur le rédactionnel
#    (éditer CONFIGS["test"] dans configs.py)
python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --ids q12 --config test
# 3. comparer les scores console / Confident AI
```

> ⚠️ Le cache est indexé par (question + modèles). Changer de **modèle** relance donc
> automatiquement. Mais si vous modifiez le **prompt** d'un agent (sans changer le modèle),
> le cache ne le « voit » pas → ajoutez `--force` pour recalculer.

---

## 6. Comparer plusieurs modèles d'un coup (qualité × coût × latence)

C'est l'outil dédié à l'objectif « quel modèle est le meilleur » :

```bash
# comparaison coût/latence + couverture d'articles (gratuit, déterministe)
python -m eval.compare --dataset ../golden_dataset_3.xlsx --configs baseline gemini3-pro claude

# en ajoutant la note de qualité par juge LLM (plus coûteux)
python -m eval.compare --dataset ../golden_dataset_3.xlsx \
    --configs baseline claude --quality-judge gpt-4o --limit 10
```

**Sortie** : une **table** triée (console) + le fichier **`eval/comparison.csv`** :

```
       config    n  article_recall   element_cov  cost_total($)   cost/q($)  latence_moy(s)
     baseline   10           0.78          0.91         1.7280     0.17280          191.6
       claude   10           0.81          0.93         2.4100     0.24100          150.2
```

Et côté **Langfuse**, chaque config est taggée → vue côte-à-côte coût/latence native.

---

## 7. Comprendre les 3 métriques

| Métrique | Type | Ce que le juge reçoit | Mesure |
|---|---|---|---|
| **Couverture des articles** | LLM pur (pas de regex) | question + **articles attendus** + **sources conservées après ranking** (titre + URL uniquement) | % des articles attendus présents dans les sources |
| **Couverture des éléments attendus** | GEval (LLM) | question + **réponse rédactionnel (complète, non tronquée)** + **éléments attendus** | % des éléments attendus présents dans la réponse |
| **Faithfulness** | deepeval (LLM) | réponse + sources scrapées (tronquées) | la réponse n'invente pas de faits hors sources |

Chaque juge ne reçoit **que** ces entrées (le juge articles ne voit pas la réponse rédigée ;
le juge éléments ne voit pas les sources). Seuil de réussite par défaut : 0.70.

**Coût + temps par question** sont mesurés (pas de juge). Tout est réuni dans le fichier
`eval/per_question_<config>.csv` écrit en fin de run : `id, difficulté, domaine, cout_usd,
temps_s, elements_pct, articles_pct, faithfulness_pct`.

À propos de la **couverture des articles** :
- Elle évalue la **recherche** (les sources que le pipeline a remontées), pas la rédaction.
- Le matching est **hybride** : d'abord un regex (gratuit), puis — pour les articles non
  trouvés — un **juge LLM** qui « voit » s'ils sont présents (gère les URLs Legifrance
  opaques type `LEGIARTI…`, les variantes BOFiP, les intitulés équivalents). Le juge utilisé
  est celui de `--judge`. Pour désactiver et rester en regex pur : `--article-regex-only`.
- Certaines cellules « Sources attendues » du golden sont des **descriptions en prose**
  (ex. « Barème kilométrique publié annuellement… ») plutôt que des réfs : le juge LLM
  peut en rattraper une partie, mais reformuler en vraies réfs (`BOI-…`, `art. … CGI`)
  reste plus fiable.

---

## 8. Astuces / dépannage

- **Cache** : `eval/.cache/`. Vider : `python -c "from eval.cache import clear_cache; print(clear_cache())"`.
- **Coût d'un run** : visible en fin de `run_eval` (« Pipeline — coût total … ») et par agent
  dans `test_pipeline.py`.
- **Tarifs des modèles preview** (`gemini-3-*`, `gpt-5.2`) : non connus de LiteLLM → coût 0
  tant que non renseignés dans `CUSTOM_PRICING` ([model_registry.py](../utils/model_registry.py)).
- **Juge provider-agnostique** : `--judge claude-opus-4-8` ou `--judge gemini-3-pro-preview`
  fonctionnent (le juge passe par la même couche LiteLLM).
- **Tout casse au démarrage ?** Vérifier les clés : `python -c "from dotenv import load_dotenv; load_dotenv(); import os; print({k: bool(os.getenv(k)) for k in ['OPENAI_API_KEY','GOOGLE_API_KEY','ANTHROPIC_API_KEY','SERPAPI_API_KEY']})"`.
