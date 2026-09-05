# Operations

## Build and run

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

hawa ingest        # one fetch cycle
hawa serve         # API + dashboard on :8000
```

Python 3.10+. SQLite by default; set `HAWA_DATABASE_URL` for Postgres and
install the `postgres` extra.

## Test, lint, types

```bash
pytest
ruff check .
mypy
```

The suite runs offline: `tests/conftest.py` blocks the real HTTP transport, so a
test that accidentally reaches the network fails loudly rather than becoming
flaky in CI.

## Configuration

Secrets and bootstrap in the environment (`HAWA_*`, see `.env.example`).
Everything that changes more often than the code goes in the DB:

```bash
hawa settings show
hawa settings set pollen_alert_threshold 10000
hawa settings set enabled_sources '["pmd_pollen","openaq"]'
hawa settings set pmd_pollen_url 'https://weather.gov.pk/rnd/pollen-data'
```

Changes apply within 60 seconds (settings cache TTL). No restart.

## Observing

- `GET /livez` — process is up. Point orchestrator liveness probes here.
- `GET /healthz` — ingest is actually working *and* the data is fresh. 503 when
  not. Point alerting here, not at `/livez`.
- `GET /v1/health/sources` — per-source detail with failure stages.
- `hawa health` — the same report on the command line, exit 1 when unhealthy.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `/healthz` 503, `failure_stage: parse` | PMD changed the page shape | Inspect the stored snapshot body, fix `parsers/pmd.py`, then `hawa reparse` |
| `/healthz` 503, reason mentions "stale" | Fetches fine, upstream stopped publishing | Check the page by hand; nothing to fix in code |
| `failure_stage: network`, HTTP 500 | PMD host is down (happens often) | Wait, or point at another PMD host via `hawa settings set pmd_pollen_url` |
| Source shows `available: false` | Missing API key | Set `HAWA_PURPLEAIR_API_KEY` / `HAWA_OPENAQ_API_KEY` |
| Alerts not sending | `alerts_enabled` false, or no token | `hawa settings show`; check `HAWA_TELEGRAM_BOT_TOKEN`; inspect `GET /v1/alerts` for `last_error` |
| `DetachedInstanceError` | Reading ORM attrs after `session_scope()` exits | Landmine #2 |

## Recovering a mangled day

```bash
hawa reparse --source pmd_pollen        # replay snapshots that failed to parse
hawa reparse --all --since 2026-04-01   # replay everything in a window
```

This works only because the raw bodies are stored and every write is idempotent.

## Deploying

Any single container. Run `hawa serve --host 0.0.0.0`. The scheduler runs
in-process, so **run exactly one instance** unless you set
`HAWA_ENABLE_SCHEDULER=false` on all but one. Concurrent ingest is safe (the
writes are idempotent) but it is pointless load on PMD.
