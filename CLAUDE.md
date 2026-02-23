# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a French tax assistant (Assistant Fiscal Intelligent) - a Streamlit application that answers French tax questions using AI agents and official legal sources. The application uses a multi-agent architecture to analyze questions, search official sources, and generate comprehensive answers with legal references.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
streamlit run streamlit_app.py

# Or use the helper script (activates venv, loads .env, runs app)
./run.sh
```

## Required API Keys

Set these in `.env` file or Streamlit secrets (`.streamlit/secrets.toml`):
- `OPENAI_API_KEY` - For GPT models (orchestrateur, generaliste, ranker)
- `GOOGLE_API_KEY` - For Gemini models (analyste, specialises, verificateur, redactionnel, jurisprudence_dork, suivi)
- `SERPAPI_API_KEY` - For web search on official French legal sources
- `SUPABASE_URL` + `SUPABASE_KEY` - For conversation history and feedback storage
- `FIRECRAWL_API_KEY` - For fallback scraping of JavaScript-heavy sites (optional)

## Architecture

### Agent Pipeline (streamlit_app.py)

The question processing follows this sequential pipeline:

1. **Agent Analyste** (Gemini) - Deep technical analysis: identifies T0 (current) and T+1 (future) regimes, generates search axes (`axes_de_recherche_serp`), flags `points_de_vigilance_legiste`
2. **Agent Orchestrateur** (OpenAI) - Routes to 1-4 specialized agents based on scores (threshold >= 0.6). Triggers mandatory `AGENT_DROIT_EUROPEEN` if EU/international compliance is detected.
3. **Specialized Agents** (Gemini, parallel) - **11 domain experts** that identify relevant legal sources (5 categories each: `textes_legaux`, `bofip`, `jurisprudence`, `reponse_ministerielle`, `autres`):
   - `AGENT_PARTICULIERS_REVENUS` - Personal income tax (IR, BIC/BNC, pensions)
   - `AGENT_TVA_INDIRECTES` - VAT and indirect taxes
   - `AGENT_ENTREPRISES_IS` - Corporate tax (IS, integration, dividends)
   - `AGENT_PATRIMOINE_TRANSMISSION` - Wealth and inheritance (IFI, Dutreil, DMTG, trusts)
   - `AGENT_STRUCTURES_MONTAGES` - Complex structures and anti-abuse rules (L64 LPF)
   - `AGENT_INTERNATIONAL` - International tax, exit tax, conventions, stable establishments
   - `AGENT_DROIT_EUROPEEN` - EU law (CJUE decisions, directives, fundamental freedoms)
   - `AGENT_IMMOBILIER_URBANISME` - Real estate (VAT on margin, DMTO, merchant builders)
   - `AGENT_PROCEDURE_CONTENTIEUX` - Procedures and litigation (burden of proof, control)
   - `AGENT_TAXES_LOCALES` - Local taxes (TH, TF, CFE)
   - `AGENT_PRELEVEMENTS_SOCIAUX` - Social contributions (CSG, CRDS, coordination)
4. **Agent Vérificateur** (Gemini) - Validates, deduplicates, and normalizes all specialist outputs. Audits coverage against analyste `points_de_vigilance_legiste`.
5. **Agent Généraliste** (OpenAI) - Generates 7 categories of Google-optimized search queries with `site:` restrictions (legal texts, BOFiP, jurisprudence split 50% historical/50% recent, ministerial responses, CJUE, constitutional, unresolved markers)
6. **Agent Jurisprudence Dork** (Gemini) - Generates specialized Google Dork queries targeting `courdecassation.fr`
7. **SerpAPI Search** - Searches official French legal domains in parallel (max 8 workers)
8. **Deduplication** - Removes duplicate URLs from search results
9. **Agent Ranker** (OpenAI) - Scores results (keep threshold >= 0.8, fallback >= 0.6 if no results). Scores 1.0 if present in both analyste diagnostics AND specialist outputs.
10. **Scraper** (`LegalScraper` + Firecrawl fallback, max 5 threads) - Extracts content from ranked sources
11. **Agent Rédactionnel** (Gemini, streaming) - Generates final structured answer with legal references

Follow-up questions use **Agent Suivi** (Gemini) which reuses `contexte_conversation` instead of the full pipeline. Returns `necessite_nouvelle_recherche: bool` to trigger full pipeline when needed.

### Key Data Flows

- All agents return JSON responses (use `lire_json_beton` from `utils/json_utils.py` for robust parsing)
- Use `clean_json_codefence` for OpenAI responses
- Search results are structured dicts with: `title`, `url`, `snippet`, `source_domain`, `position`, `query`
- Ranked results include: `keep` (bool), `score` (float), `reason` (str)
- Scraper adds a `content` field to ranked docs; Supabase storage strips full content (keeps 200-char preview)

### Official Sources

Searches are restricted to these French legal domains (`utils/search.py`):
- `legifrance.gouv.fr`, `bofip.impots.gouv.fr`, `conseil-etat.fr`, `courdecassation.fr`, `conseil-constitutionnel.fr`, `assemblee-nationale.fr`, `senat.fr`, `fiscalonline.fr`, `europa.eu` (CJUE)

Domain matching is strict: exact match or subdomain only (prevents fake domains). `europa.eu` gets up to 5 results per query and adds `-filetype:pdf`.

### Model Configuration

Default models per agent are defined in `streamlit_app.py` (`DEFAULT_MODELS`). Users can override via the Streamlit sidebar. Model names map to actual API model IDs via `MODEL_MAPPING`.

- **Gemini agents:** analyste, redactionnel, specialises, suivi, verificateur, jurisprudence_dork → default `gemini-2.5-flash` family
- **OpenAI agents:** orchestrateur, generaliste, ranker → default `gpt-4o` family

### Persistence & Auth (Supabase)

- **`utils/conversations.py`** - Save/list/load/delete conversations. Strips heavy `content` fields on save. Supports `user_email` filtering for multi-user access.
- **`utils/feedback.py`** - Collect thumbs up/down ratings, optional comments, sources count, follow-up flag. Requires a `feedbacks` table in Supabase. Integrates Supabase Auth (email/password).

### Scraping Strategy (`utils/scraper_utils.py`)

1. **Primary:** `LegalScraper` (`legal_scraper.py`) - custom scraper with trafilatura, supports all official domains, 0.3s rate limit delay
2. **Fallback:** Firecrawl API for JavaScript-heavy pages (cleans jsessionid/cid params before calling)

## Code Patterns

- Agent functions accept `api_key` and `model_name` parameters
- All agent prompts request strict JSON output with no surrounding text
- Use `clean_json_codefence` for OpenAI responses, `lire_json_beton` for Gemini/robust parsing
- Session state manages: `messages`, `contexte_conversation`, `processing`, `active_domains`, `agent_models`
- Active domains are passed through the entire pipeline (generaliste → search → ranker) and controlled via sidebar checkboxes
