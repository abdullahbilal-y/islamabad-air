# Architecture

Single Python process: FastAPI app + APScheduler background job + SQLite (or
Postgres). No broker, no workers, no queue — one government page updated once a
day does not justify any of that, and the operational cost would exceed the
problem.

## Components

| Component | Path | Responsibility |
|---|---|---|
| Sources | `src/hawa/sources/` | Fetch bytes; parse bytes. Two separate methods, on purpose (see below). |
| Parsers | `src/hawa/parsers/` | Pure functions over a response body. No I/O, so they are testable from a saved fixture. |
| Ingest | `src/hawa/ingest.py` | Orders the pipeline: fetch → store raw → parse → idempotent upsert. |
| Health | `src/hawa/health.py` | Outcome-based health: success ratio *and* data freshness. |
| Settings store | `src/hawa/settings_store.py` | DB-row config with TTL cache and last-known-good. |
| Alerts | `src/hawa/alerts/` | `engine.py` decides; `channels.py` delivers. |
| API | `src/hawa/api/` | Read endpoints, subscription management, health. |
| Scheduler | `src/hawa/scheduler.py` | 30-minute poll plus a once-per-boot catch-up. |
| CLI | `src/hawa/cli.py` | `ingest`, `reparse`, `serve`, `export`, `settings`, `health`. |

## Why fetch and parse are separate methods

`Source.fetch` is the only thing allowed to touch the network. Whatever it
returns is persisted **and committed** before `Source.parse` runs. That gives:

- a parse bug never loses irreplaceable data (`hawa reparse` replays snapshots);
- `parse` is pure, so every source can be tested offline from a real captured
  response — and each one ships one in `tests/fixtures/`.

## Why health is not a boolean

`FetchOutcome` records one row per *attempt at the real work*, with the stage
that failed (`network` / `http` / `parse`). Health is then two things at once:

- **success ratio** over a rolling window — catches "fetch works, parse doesn't";
- **freshness** of the newest observation — catches "everything works, upstream
  stopped publishing".

Both must be green. `/healthz` returns 503 otherwise. `/livez` is separate and
reports only that the process is alive, because restarting cannot fix a website
changing its HTML.

## Data model

`raw_snapshot` (the bytes) → `pollen_reading` / `air_reading` (parsed, natural
key) → `alert_event` (dedup key) → delivery. Plus `setting` (runtime config) and
`fetch_outcome` (health). See `src/hawa/models.py`.
