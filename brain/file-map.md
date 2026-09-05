# File map

| Path | Purpose |
|---|---|
| `src/hawa/config.py` | Env-backed bootstrap config and secrets. Only things that must exist before the DB does. |
| `src/hawa/settings_store.py` | Runtime-swappable config in a DB row: thresholds, source URLs, enabled sources. |
| `src/hawa/models.py` | SQLModel schema. The unique constraints here are load-bearing — read the module docstring. |
| `src/hawa/db.py` | Engine, session scope. Note `session_scope()` expires objects on exit (landmine #2). |
| `src/hawa/ingest.py` | The pipeline and its ordering. The most important file in the repo. |
| `src/hawa/health.py` | Outcome + freshness health computation. |
| `src/hawa/parsers/pmd.py` | The PMD parser: three strategies, raises rather than returning empty. |
| `src/hawa/sources/base.py` | The `Source` contract. Start here to add a source. |
| `src/hawa/sources/pmd_pollen.py` | PMD pollen. **Verified live.** |
| `src/hawa/sources/purpleair.py` | PurpleAir bbox query. Not yet run against a live key. |
| `src/hawa/sources/openaq.py` | OpenAQ v3, one call per metric (not per monitor). Not yet run against a live key. |
| `src/hawa/alerts/engine.py` | Threshold evaluation and dispatch with retry. |
| `src/hawa/alerts/channels.py` | Telegram and webhook senders. Add channels here. |
| `src/hawa/api/routes.py` | Every HTTP endpoint. |
| `src/hawa/api/schemas.py` | The public response contract. |
| `src/hawa/main.py` | App factory, lifespan, dashboard route, `/livez`. |
| `src/hawa/scheduler.py` | Poll job and boot catch-up. |
| `src/hawa/cli.py` | The `hawa` command. |
| `src/hawa/web/templates/dashboard.html` | The dashboard. No build step, no framework. |
| `tests/fixtures/pmd_pollen_2026-09-05.html` | A real PMD page, byte-for-byte. Do not tidy it. |
| `tests/conftest.py` | Isolated-DB fixture and the network guard. |
| `brain/` | This directory. |
