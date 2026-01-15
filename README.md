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

### En local

```bash
streamlit run app.py
```

L'application s'ouvrira dans votre navigateur à l'adresse `http://localhost:8501`

### Déploiement

L'application peut être déployée sur :

- Streamlit Cloud
- Heroku
- AWS
- Tout autre service supportant Streamlit

Pour Streamlit Cloud :

1. Pousser le code sur GitHub
2. Se connecter à [share.streamlit.io](https://share.streamlit.io)
3. Connecter le dépôt
4. Ajouter les secrets dans la configuration

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
│   ├── json_utils.py           # Parsing JSON robuste
│   ├── search.py               # Recherche SerpAPI
│   └── scraper_utils.py        # Utilitaires de scraping
├── legal_scraper.py            # Scraper pour sites juridiques
├── requirements.txt            # Dépendances Python
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
