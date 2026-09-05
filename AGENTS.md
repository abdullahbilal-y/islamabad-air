# Hawa — notes for coding agents

Open pollen and air-quality API for Islamabad. Python 3.10+, FastAPI, SQLModel.

## Read this first

**[brain/landmines.md](brain/landmines.md)** — non-obvious traps, each with the
why. Read it before touching the scraper, the ingest path, or anything that uses
a database session. Then:

- [brain/system-overview.md](brain/system-overview.md) — what this does.
- [brain/architecture.md](brain/architecture.md) — components and flow.
- [brain/file-map.md](brain/file-map.md) — where things live.
- [brain/operations.md](brain/operations.md) — run, test, debug, deploy.
- [brain/glossary.md](brain/glossary.md) — domain terms.

## The three rules that most of this codebase exists to enforce

1. **Store the raw response before parsing it.** PMD publishes one figure a day
   and keeps no archive. A parse bug at fetch time loses that day permanently.
2. **A 200 OK with zero rows is a failure, not an empty day.** The parser raises;
   health goes red. Never "fix" a failing parse by returning empty.
3. **Every write is idempotent on a natural key.** That is what makes retries,
   the boot catch-up, and `hawa reparse` safe.

## Commands

```bash
pytest            # 52 tests, offline, ~5s
ruff check .
mypy
hawa ingest       # live fetch
hawa health
```

## Conventions

- Comments explain *why*, especially where the obvious implementation is wrong.
- New sources: see CONTRIBUTING.md. Fixture + parser test are required.
- Do not lower the poll interval.
- Update `brain/` in the same change that changes behaviour.
