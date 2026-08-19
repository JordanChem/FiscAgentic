# Assistant Fiscal Intelligent

Application Streamlit pour répondre aux questions fiscales françaises en utilisant l'IA et des sources officielles.

## 🚀 Installation

1. **Créer un environnement virtuel** (recommandé) :
   python3 -m venv venv
   source venv/bin/activate

## 🎯 Fonctionnalités

- **Chat conversationnel** : Interface de chat moderne avec historique des conversations
- **Questions de suivi** : Poursuivez la discussion avec des questions de suivi basées sur le contexte
- **Analyse intelligente** : Comprend et analyse les questions fiscales complexes
- **Sources officielles** : Recherche dans Legifrance, BOFiP, Conseil d'État, etc.
- **Réponse rédigée** : Génère une réponse détaillée avec références juridiques
- **Sources pertinentes** : Affiche les sources classées par pertinence
- **Nouvelle conversation** : Bouton pour réinitialiser et commencer une nouvelle discussion

## 🏗️ Architecture

Le système utilise plusieurs agents IA spécialisés :

1. **Agent Analyste** : Analyse la question et identifie les concepts clés
2. **Agent Orchestrateur** : Route vers les agents spécialisés appropriés
3. **Agents Spécialisés** : Identifient les sources juridiques pertinentes
   - Particuliers/Revenus
   - TVA/Indirectes
   - Entreprises/IS
   - Patrimoine/Transmission
   - Structures/Montages
   - International
4. **Agent Vérificateur** : Vérifie et nettoie les sources
5. **Agent Généraliste** : Génère des requêtes de recherche optimisées
6. **Agent Ranker** : Classe les résultats par pertinence
7. **Agent Rédactionnel** : Génère la réponse finale
8. **Agent de Suivi** : Répond aux questions de suivi en utilisant le contexte de la conversation

## 📋 Prérequis

- Python 3.8+
- Clés API :
  - OpenAI (pour GPT-4o)
  - Google Generative AI (pour Gemini)
  - SerpAPI (pour la recherche web)

## 🚀 Installation

1. **Cloner le dépôt** (ou utiliser le dossier actuel)

2. **Installer les dépendances** :

```bash
pip install -r requirements.txt
```

3. **Configurer les clés API** :

   Option A : Variables d'environnement

   ```bash
   export OPENAI_API_KEY="votre_clé_openai"
   export GOOGLE_API_KEY="votre_clé_google"
   export SERPAPI_API_KEY="votre_clé_serpapi"
   ```

   Option B : Secrets Streamlit (recommandé pour le déploiement)

   - Créer un fichier `.streamlit/secrets.toml` :

   ```toml
   OPENAI_API_KEY = "votre_clé_openai"
   GOOGLE_API_KEY = "votre_clé_google"
   SERPAPI_API_KEY = "votre_clé_serpapi"
   ```

## 🏃 Utilisation

Le projet expose **deux** surfaces au-dessus du même pipeline
(`pipeline/core.py`) — aucune ne réimplémente la logique métier :

| Surface | Rôle | Lancement |
|---|---|---|
| **API FastAPI** (`api/`) | Production — consommée par fiscalonline.fr | `uvicorn api.main:app --port 8080` |
| **UI Streamlit** (`streamlit_app.py`) | Debug et démonstration internes | `streamlit run streamlit_app.py` |
| **CLI** (`test_pipeline.py`) | Une question en ligne de commande | `python test_pipeline.py "…" --stream` |

### API (cible de production)

```bash
pip install -r requirements-api.txt      # sans Streamlit ni outillage d'éval
export API_SHARED_SECRET=$(openssl rand -hex 32)
uvicorn api.main:app --host 127.0.0.1 --port 8080
```

Le contrat d'intégration destiné au développeur front est dans
[docs/API.md](docs/API.md) : endpoints, schémas, séquence SSE (protocole Vercel
AI SDK), codes d'erreur, exemples `curl` et `useChat`.

L'authentification est déléguée : l'API de fiscalonline authentifie l'abonné,
puis relaie l'appel avec `X-API-Key` et `X-User-Email`.

### UI Streamlit (debug interne)

```bash
pip install -r requirements.txt          # application complète (Streamlit inclus)
streamlit run streamlit_app.py
```

Disponible sur `http://localhost:8501`.

### Déploiement

Procédure complète (systemd, nginx, recette, exploitation, rollback) :
[deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md). Une image Docker est également
fournie (`deploy/Dockerfile`).

### Tests

```bash
pytest                                   # tests unitaires + tests d'API
```

## 🧪 Tests & évaluation de la qualité

Le pipeline dispose d'un système d'évaluation (golden dataset + comparaison de modèles) :

- **Lancer une question seule** (réponse + coût + latence, sans notation) :
  ```bash
  python test_pipeline.py "ma question fiscale"
  ```
- **Noter le golden dataset** (couverture d'articles + éléments attendus + fidélité) :
  ```bash
  python -m eval.run_eval --dataset ../golden_dataset_3.xlsx --judge gpt-4o
  ```
- **Comparer des modèles** (qualité × coût × latence) :
  ```bash
  python -m eval.compare --dataset ../golden_dataset_3.xlsx --configs baseline gemini3-pro claude
  ```

Tous les appels LLM passent par une couche unique [utils/llm.py](utils/llm.py) (LiteLLM :
Gemini / OpenAI / **Anthropic**) qui capture coût/tokens/latence et trace vers Langfuse.

**Traçage bout-en-bout (app live + éval).** Si `LANGFUSE_*` est configuré, chaque question
produit **une trace** dans Langfuse : une génération par agent (analyste, orchestrateur,
spécialistes, vérificateur, généraliste, jurisprudence, ranker, rédactionnel) avec
entrée/sortie/coût/latence, regroupée par **session utilisateur** (= conversation), plus des
**spans** pour les étapes non-LLM (recherche, dédup, scraping) et les artefacts structurés
(scores de routage, sources vérifiées, distribution du ranking). Le **👍/👎** utilisateur est
attaché en score `user_feedback` → filtrer les traces mal notées pour améliorer la qualité.
Sans clés Langfuse, tout fonctionne à l'identique (aucune trace émise).

📖 **Mode d'emploi pas-à-pas** : [eval/GUIDE.md](eval/GUIDE.md) — lancer une question (avec/sans
éval), où voir les résultats, dashboards (Confident AI / Langfuse), changer les modèles.
🏗️ **Architecture du système d'éval** : [eval/README.md](eval/README.md).

## 📁 Structure du projet

```
.
├── app.py                      # Application Streamlit principale
├── agents/                     # Agents IA
│   ├── __init__.py
│   ├── analyste.py             # Agent d'analyse
│   ├── orchestrateur.py        # Agent de routage
│   ├── specialises.py          # Agents spécialisés
│   ├── generaliste.py           # Agent de génération de requêtes
│   ├── verificateur.py         # Agent de vérification
│   ├── ranker.py               # Agent de classement
│   ├── redactionnel.py         # Agent de rédaction
│   └── suivi.py                # Agent de suivi conversationnel
├── utils/                      # Utilitaires
│   ├── __init__.py
│   ├── llm.py                  # Couche LLM unique (LiteLLM) + coût/latence/tracing
│   ├── model_registry.py       # Mapping noms logiques → modèles LiteLLM (+ prix)
│   ├── api_keys.py             # Récupération centralisée des clés
│   ├── json_utils.py           # Parsing JSON robuste
│   ├── search.py               # Recherche SerpAPI
│   └── scraper_utils.py        # Utilitaires de scraping
├── pipeline/                   # Pipeline réutilisable hors Streamlit
│   └── core.py                 # run_pipeline(question, models_config) -> PipelineResult
├── eval/                       # Évaluation qualité (voir eval/GUIDE.md)
│   ├── dataset.py              # Chargement du golden dataset
│   ├── articles.py             # Matching des références d'articles
│   ├── metrics.py              # Métriques deepeval (articles, éléments, fidélité)
│   ├── configs.py              # Configs de modèles nommées
│   ├── cache.py                # Cache des exécutions du pipeline
│   ├── run_eval.py             # Notation du golden dataset
│   └── compare.py              # Comparaison de modèles (qualité × coût × latence)
├── test_pipeline.py            # CLI : lance une question hors Streamlit
├── legal_scraper.py            # Scraper pour sites juridiques
├── requirements.txt            # Dépendances (app complète — lu par Streamlit Cloud)
├── requirements-api.txt        # Dépendances du serveur de production
├── requirements-dev.txt        # + évaluation et tests
├── .env.example                # Exemple de configuration
├── .gitignore                  # Fichiers à ignorer
└── README.md                   # Ce fichier
```

## 🔧 Configuration

### Modèles utilisés

- **OpenAI** : `gpt-4o` (orchestrateur, ranker, généraliste)
- **Google Gemini** : `gemini-3-flash-preview` (analyste, spécialisés, vérificateur, rédactionnel)

### Domaines de recherche

L'application recherche uniquement dans les sources officielles :

- legifrance.gouv.fr
- bofip.impots.gouv.fr
- conseil-etat.fr
- courdecassation.fr
- conseil-constitutionnel.fr
- assemblee-nationale.fr
- senat.fr

## 💬 Utilisation du Chat

L'application utilise une interface de chat conversationnel :

1. **Première question** : Posez votre question fiscale dans le champ de chat

   - L'application effectue une recherche complète avec tous les agents
   - Les sources officielles sont recherchées et classées
   - Une réponse détaillée est générée

2. **Questions de suivi** : Après la première réponse, vous pouvez poser des questions de suivi

   - L'agent de suivi utilise le contexte de la conversation précédente
   - Plus rapide car il n'effectue pas de nouvelle recherche complète
   - Parfait pour clarifier, approfondir ou demander des précisions

3. **Nouvelle conversation** : Utilisez le bouton "🗑️ Nouvelle conversation" dans la sidebar pour réinitialiser

## ⚠️ Notes importantes

- Les clés API sont nécessaires pour faire fonctionner l'application
- Le traitement peut prendre plusieurs secondes selon la complexité de la question
- Les questions de suivi sont plus rapides car elles utilisent le contexte existant
- Les réponses sont générées par IA et doivent être vérifiées par un expert fiscal
- L'application est conçue pour la fiscalité française uniquement

## 📝 Licence

Ce projet est destiné à un usage interne/professionnel.

## 🤝 Contribution

Pour toute question ou amélioration, contactez l'équipe de développement.
