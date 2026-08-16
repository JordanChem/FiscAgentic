# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a French tax assistant (Assistant Fiscal Intelligent) that answers French tax
questions using AI agents and official legal sources. A multi-agent pipeline analyzes the
question, searches official sources, and generates an answer with legal references.

**The production target is the FastAPI service in `api/`**, consumed by a chat tab on
fiscalonline.fr (front developed separately with the Vercel AI SDK). fiscalonline's own API
acts as an auth proxy: it authenticates the subscriber, then forwards the call with an
`X-API-Key` shared secret and `X-User-Email`. Streamlit is now only an internal debug UI.

**One pipeline, three surfaces** — none of them reimplements business logic:

| Surface | Role | Entry point |
|---|---|---|
| `api/` | Production HTTP + SSE | `uvicorn api.main:app` |
| `streamlit_app.py` | Internal debug UI | `streamlit run streamlit_app.py` |
| `test_pipeline.py` | CLI, one question | `python test_pipeline.py "…" --stream` |

This matters: `streamlit_app.py` used to carry its own copy of the pipeline, which drifted
from the headless one (missing FiscalOnline branch, different jurisprudence parsing,
different models). Keeping a second live consumer of `pipeline/core.py` is what makes any
new drift immediately visible. **Never add pipeline logic outside `pipeline/`.**

## Commands

```bash
pip install -r requirements.txt          # runtime only (API) — no Streamlit
pip install -r requirements-dev.txt      # + Streamlit, deepeval, pytest

uvicorn api.main:app --port 8080         # production service
streamlit run streamlit_app.py           # debug UI
python test_pipeline.py "…" --stream     # CLI
pytest                                   # unit + API tests
```

## Required API Keys

Set these in `.env` (or Streamlit secrets for the debug UI). Full annotated list in
`.env.example`; deployment procedure in `deploy/DEPLOYMENT.md`; front-facing contract in
`docs/API.md`.

- `API_SHARED_SECRET` - Shared secret expected in `X-API-Key` (comma-separated values allowed, for rotation)
- `OPENAI_API_KEY` - For GPT models (orchestrateur, generaliste, ranker)
- `GOOGLE_API_KEY` - For Gemini models (analyste, specialises, verificateur, redactionnel, jurisprudence_dork, suivi)
- `SERPAPI_API_KEY` - For web search on official French legal sources
- `SUPABASE_URL` + `SUPABASE_KEY` - For conversation history and feedback storage
- `FIRECRAWL_API_KEY` - For fallback scraping of JavaScript-heavy sites (optional)

## Architecture

### Agent Pipeline (pipeline/core.py)

`run_pipeline_stream()` is the **single** implementation; `run_pipeline()` merely drains it
with `stream_redaction=False` (blocking rédactionnel, `json_mode=True` — the eval path is
unchanged). It yields `StepEvent` / `SourcesEvent` / `TextDelta` / `ResultEvent`
(`pipeline/events.py`).

The question processing follows this sequential pipeline:

1. **Agent Analyste** (Gemini) - Deep technical analysis: identifies T0 (current) and T+1 (future) regimes, generates search axes (`axes_de_recherche_serp`), flags `points_d_attention_legiste`
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
4. **Agent Vérificateur** (Gemini) - Validates, deduplicates, and normalizes all specialist outputs. Audits coverage against analyste `points_d_attention_legiste`.
5. **Agent Généraliste** (OpenAI) - Generates 7 categories of Google-optimized search queries with `site:` restrictions (legal texts, BOFiP, jurisprudence split 50% historical/50% recent, ministerial responses, CJUE, constitutional, unresolved markers)
6. **Agent Jurisprudence Dork** (Gemini) - Generates specialized Google Dork queries targeting `courdecassation.fr`
7. **SerpAPI Search** - Searches official French legal domains in parallel (max 8 workers)
8. **Deduplication** - Removes duplicate URLs from search results
9. **Agent Ranker** (OpenAI) - Scores results (keep threshold >= 0.8, fallback >= 0.6 if no results). Scores 1.0 if present in both analyste diagnostics AND specialist outputs.
10. **Scraper** (`LegalScraper` + Firecrawl fallback, max 5 threads) - Extracts content from ranked sources
11. **Agent Rédactionnel** (Gemini, streaming) - Generates final structured answer with legal references

Follow-up questions use **Agent Suivi** (`pipeline/followup.py`), which reuses
`contexte_conversation` instead of the full pipeline. Its answer is buffered, not streamed,
so `necessite_nouvelle_recherche` can be read **before** anything reaches the client: when
it is true, the service transparently chains into the full pipeline (`escalated: true` in
`data-meta`) instead of dead-ending the turn.

`build_contexte()` refreshes the context after **every** turn. The old app only wrote it
after the first question, so long conversations kept answering against the first exchange.

### Key Data Flows

- All agents return JSON responses (use `lire_json_beton` from `utils/json_utils.py` for robust parsing)
- Use `clean_json_codefence` for OpenAI responses
- Search results are structured dicts with: `title`, `url`, `snippet`, `source_domain`, `position`, `query`
- Ranked results include: `keep` (bool), `score` (float), `reason` (str)
- Scraper adds a `content` field to ranked docs; Supabase storage strips full content (keeps 200-char preview)
- **`content` never leaves the process**: `pipeline/events.public_sources()` projects sources
  onto publishable fields. FiscalOnline / JusticeLibre entries carry whole articles.
- The rédactionnel streams **raw JSON**, not markdown (and without `json_mode`, so it may
  arrive fenced). `pipeline/normalizer.RedactionNormalizer` extracts `reponse_redigee`
  incrementally so consumers receive clean markdown. Never stream its chunks directly.

### Official Sources

Searches are restricted to these French legal domains (`utils/search.py`):
- `legifrance.gouv.fr`, `bofip.impots.gouv.fr`, `conseil-etat.fr`, `courdecassation.fr`, `conseil-constitutionnel.fr`, `assemblee-nationale.fr`, `senat.fr`, `fiscalonline.fr`, `europa.eu` (CJUE)

Domain matching is strict: exact match or subdomain only (prevents fake domains). `europa.eu` gets up to 5 results per query and adds `-filetype:pdf`.

### Model Configuration

Production defaults live in `pipeline/core.py` (`DEFAULT_MODELS`, mostly Claude), shared by
the API and the debug UI. Logical names resolve to LiteLLM ids via `utils/model_registry.py`.

`eval/configs.py` deliberately keeps its **own** frozen base (Gemini / GPT-4o): it feeds
`eval/cache.py::_key()`, so importing the production defaults would invalidate the whole
eval disk cache on every production model change. Changing the eval baseline is a separate,
explicit decision.

### Persistence & Auth (Supabase)

- **`services/supabase.py`** - Shared `@lru_cache` client. Never call `create_client` directly.
- **`utils/conversations.py`** - Save/list/load/delete conversations. Strips heavy `content` fields on save.
- **`utils/feedback.py`** - Thumbs up/down + optional comment; also attaches the rating as a
  Langfuse score via `trace_id`.

⚠️ **User isolation is purely application-level** (`.eq("user_email", …)`); there is no RLS.
Every query must be scoped by the identity from `X-User-Email` — never by a value from the
request body. A missing filter would expose every subscriber's conversations.

### Scraping Strategy (`utils/scraper_utils.py`)

1. **Primary:** `LegalScraper` (`legal_scraper.py`) - custom scraper with trafilatura, supports all official domains, 0.3s rate limit delay
2. **Fallback:** Firecrawl API for JavaScript-heavy pages (cleans jsessionid/cid params before calling)
3. **Global budget** (`SCRAPE_TOTAL_TIMEOUT_S`, default 120s) - the step returns partial
   results rather than blocking. Per-call timeouts are not enough: trafilatura extraction is
   pure computation with no timeout, and a large BOFiP page once hung a run for 30+ minutes.

## Code Patterns

- Agent functions accept `api_key` and `model_name` parameters
- All agent prompts request strict JSON output with no surrounding text
- Use `clean_json_codefence` for OpenAI responses, `lire_json_beton` for Gemini/robust parsing
- Active domains are passed through the entire pipeline (generaliste → search → ranker)
- Thread hand-offs must use `copy_context().run` — the Langfuse run context is a `ContextVar`
  and does not cross thread boundaries on its own; without it, costs are silently lost.

### API-specific patterns (`api/`)

- **Never hand a sync generator to `StreamingResponse`.** Starlette iterates it via
  `anyio.to_thread.run_sync` **per item**, and anyio copies the context on each call — so
  `ContextVar` mutations are discarded between yields. The Langfuse trace would silently
  record zero cost. `api/runner.py` runs the whole pipeline in one dedicated thread and
  bridges events with `loop.call_soon_threadsafe`. Its module docstring has the details.
- SSE endpoints must be `async def` (only async generators receive the disconnect
  `CancelledError`); Supabase-touching endpoints must be plain `def` (blocking client).
- `api/sse.py` is the only place that knows the AI SDK wire format (v5/v4 switchable).
