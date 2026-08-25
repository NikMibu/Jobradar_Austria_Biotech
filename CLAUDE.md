# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Heimspiel is a personal job-radar pipeline for life-science jobs in Austria (see `README.md`). It's a Python CLI (SQLite, no server) that scrapes/extracts/scores job postings, computes public-transit travel times, and exports JSON consumed by a static Vite/MapLibre frontend in `site/`. `heimspiel_SPEC.md` is the canonical spec — module docstrings and code comments reference it by section (e.g. "SPEC §6"); when behavior is unclear or you're changing it, check the relevant SPEC section first.

## Commands

```bash
uv sync --extra scrape          # install deps (scrape extra needed for JobSpy/trafilatura sources)
uv run ruff check src tests     # lint (CI gate)
uv run pytest -q                # full test suite
uv run pytest tests/test_locations.py -v            # single file
uv run pytest tests/test_locations.py::test_name -v # single test
uv run heimspiel <command>      # run the CLI, e.g. `heimspiel daily`, `heimspiel --help`
```

Frontend (`site/`):
```bash
npm install
npm run dev       # vite dev server
npm run build     # tsc --noEmit && vite build (both are the CI gate)
```

Local/zero-cost LLM backend instead of the Anthropic API:
```bash
export HEIMSPIEL_LLM=ollama       # default: anthropic
export HEIMSPIEL_MODEL=qwen2.5:7b # default per backend, see llm.py
export HEIMSPIEL_OLLAMA_URL=http://localhost:11434
```
`HEIMSPIEL_ROOT` overrides the repo root (used by tests/foreign checkouts) and `HEIMSPIEL_DB` overrides the SQLite file path — set it to a scratch file when comparing LLM backends, since the extraction cache doesn't key on model/backend.

## Architecture

**Pipeline** (`src/heimspiel/cli.py`, orchestrated by `heimspiel daily`, each stage is also its own subcommand):
```
fetch → extract → locations → companies(--geocode) → travel → score → export → report
```
- `fetch`: adapters in `sources/` (`jobspy_src`, `karriere_at`, `biotechjobs`, `vbc`, `career_pages`, common helpers in `sources/base.py`) write into `postings_raw`; `normalize.dedup()` marks cross-source duplicates via fuzzy title match within a 60-day window.
- `extract`: LLM call per posting into the fixed `Extraction` schema (`extract.py`). Cached on `content_hash + schema_version` — a posting is only re-extracted if its content or the schema changes. Backfills over 500 postings use the Anthropic Batch API.
- `locations`: resolves each posting's free-text `location_text` to a `sites` row (LLM-normalized city, cached per distinct string in `location_cache`; prefers an existing curated company site over creating a generic one). This is what feeds `lat`/`lon` and the travel-time filter — without it, `site_id` stays `NULL` and downstream travel/map data is empty.
- `companies --geocode`: syncs `config/companies.yaml` into `companies`/`sites`, then geocodes any `sites` row that has `address_text` but no `lat`/`lon` via Nominatim (rate-limited, results are proposals — "prüfen!" — not verified truth).
- `travel`: computes transit minutes for every `(site, anchor)` pair without a cache entry via the public Transitous API (rate-limited; `--rebuild` clears the cache after a GTFS schedule change).
- `score`: hard filters (free, deterministic — PhD requirement, seniority, role family, travel-time limit) then an LLM fit score, but only for postings that pass the hard filters.
- `export` / `report`: `export.py` writes `site/public/data/{jobs,companies,meta}.json` for the frontend; `report.py` writes a daily markdown digest to `data/report-<date>.md`.

**Ordering matters**: stages that consume `sites`/`site_id` (`travel`, `score`) must run after stages that populate it (`locations`, `companies --geocode`) in the *same* run, or a stage sees stale/missing data for one cycle.

**Data model** (`db.py`): single SQLite file, plain-list migrations gated by `PRAGMA user_version` (`MIGRATIONS: list[list[str]]`, one list = one version bump, applied in order). SQLite has no `ALTER COLUMN`, so nullability/constraint changes go through the create-new-table/copy/drop/rename pattern (see migration 2 for the template). Two config-driven inputs live outside the DB and are synced in: `config/profile.local.yaml` (personal profile/filters/commute anchors — gitignored, copy from `profile.example.yaml`) and `config/companies.yaml` (curated employer sites, gitignored-by-convention but not gitignored by mechanism — hand-maintained because job-ad location text often names the work site, not the employer's actual office).

**LLM backend abstraction** (`llm.py`): a single `parse_structured()` function dispatches to Anthropic (`messages.parse`, prompt-cached) or Ollama (`/api/chat` with JSON-schema `format=`) based on `HEIMSPIEL_LLM`; `extract.py`, `match.py`, and `locations.py` all call it identically. When testing code that calls it, monkeypatch at the call site's own module boundary (e.g. `monkeypatch.setattr(locations, "resolve_city", ...)` or `monkeypatch.setattr(module, "extract_one", ...)`), not inside `llm.py` — that's the pattern used throughout `tests/`.

**Caching convention**: every LLM-backed stage caches on a normalized key so reruns are cheap and idempotent — `extract.py` on `content_hash + schema_version`, `locations.py` on `normalize.norm_text(location_text) + schema_version`. Bump the module's own `*_SCHEMA_VERSION` constant to force re-processing after a prompt/schema change; don't add a separate invalidation mechanism.

**Not part of the codebase**: `applications/` at the repo root is the user's personal job-application archive (unrelated tooling, real PII) — it's gitignored and out of scope unless a task explicitly asks about it.
