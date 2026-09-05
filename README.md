# Hawa — Islamabad Pollen & Air Quality API

**An open API for data that already exists but nobody can use.**

Every spring, Islamabad gets one of the worst paper mulberry pollen seasons
anywhere in the world. Every winter, it gets smog. The Pakistan Meteorological
Department actually measures the pollen — daily, sector by sector, for H-8, E-8,
G-6 and F-10 — and publishes the numbers on a web page.

That page renders its table in JavaScript. So the numbers are, in practice,
unavailable: you cannot query them, you cannot get yesterday's, you cannot chart
a season, and you certainly cannot build an app that texts an asthmatic person
when their sector spikes.

Hawa scrapes that page, keeps the history, and serves it as a plain REST API
with an OpenAPI schema, a dashboard, and a threshold alert engine.

> Independent open-source project. **Not** affiliated with or endorsed by PMD.
> Not medical advice.

---

## Quick start

```bash
git clone https://github.com/abdullahbilal-y/islamabad-air
cd islamabad-air

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

hawa ingest      # fetch today's PMD page and store it
hawa serve       # http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000> for the dashboard, or `/docs` for the
interactive API reference.

No database to set up — it uses SQLite by default. Point `HAWA_DATABASE_URL` at
Postgres when you outgrow that.

## The API

| Endpoint | What it gives you |
|---|---|
| `GET /v1/pollen/latest` | The most recent day: every pollen type in every reporting sector, plus per-sector totals and severity bands |
| `GET /v1/pollen/{date}` | One specific day |
| `GET /v1/pollen?start=&end=&sector=&pollen_type=` | Range query — this is the one that makes seasons chartable |
| `GET /v1/pollen/sectors/summary` | Per-sector totals and bands for a day |
| `GET /v1/air/latest?metric=pm25` | Latest reading per air sensor |
| `GET /v1/sources` | Every source, whether it's enabled, and why it isn't running if it isn't |
| `POST /v1/subscriptions` | Subscribe a Telegram chat or a webhook to threshold alerts |
| `GET /healthz` | Whether ingest is actually *working* (see below) |

Example:

```bash
curl -s localhost:8000/v1/pollen/latest | jq '.sectors'
```

```json
[
  {
    "sector": "H-8",
    "observed_date": "2026-09-04",
    "total": 76,
    "band": "absent",
    "top_type": "Cannabis",
    "top_value": 36,
    "types_reported": 8
  }
]
```

For bulk analysis, skip the API: `hawa export --out pollen.csv` writes the whole
history in one file.

## What's actually verified

Being straight about this, because a scraper's README is exactly where people
overclaim:

- **PMD pollen source — verified live** on 2026-09-05 against
  `https://weather.gov.pk/rnd/pollen-data`. The parser test runs against a
  byte-for-byte capture of that real page, checked into `tests/fixtures/`.
  It correctly reads all 8 pollen types PMD tracks and all 4 sector columns.
- **PurpleAir and OpenAQ sources — written against their documented APIs, not
  yet run against a live key.** They ship with unit tests over synthetic
  responses, and they report themselves unavailable when no key is configured
  rather than pretending to work. If you have a key, running them and reporting
  back is a genuinely useful first contribution.
- Note that `rnd.pmd.gov.pk` and `namc.pmd.gov.pk` were both returning HTTP 500
  during development. `weather.gov.pk` was the host that worked. The URL lives
  in a settings row precisely because this keeps happening.

## Design: three decisions that shape everything

Scraping a government page you have no agreement with is a specific engineering
problem, and most of this codebase is a response to it.

**1. The raw bytes are saved before anything parses them.**
Today's pollen count exists for exactly one day. If our parser is broken when we
fetch it, PMD will not re-publish it for us — the number is gone forever. So
`ingest` persists the fetched body and commits, *then* parses in a separate
try/except. Fix the parser later and `hawa reparse` replays the stored snapshots
and recovers the days you would otherwise have lost.

**2. A 200 OK with zero rows is treated as a failure.**
When PMD redesigns the page, the fetch keeps succeeding and the parser silently
finds nothing. "No pollen today" and "our scraper is broken" look identical from
the outside, and only one of them is safe to serve during peak season. So the
parser raises rather than returning empty, health goes red, and `/healthz`
returns 503.

**3. Health is measured on outcomes, not on connections.**
`/healthz` tracks how many fetch attempts actually produced rows over a rolling
window, *and* how old the newest reading is. Both have to be good. A green
process serving three-week-old pollen counts is the failure this catches, and it
is invisible to any check that only asks "is the server up?".
(`/livez` is separate and deliberately dumb — restarting the container does not
fix a government website changing its HTML.)

A fourth, smaller one: **values are stored exactly as printed.** PMD prints
`"00"`, not `0`. If a cell is something we cannot read, we keep the text, set
the number to null, and flag it for review — we never guess a plausible-looking
number, because a wrong number nobody questions is worse than a visible gap.

## Alerts

```bash
hawa subscribe telegram 123456789 --sectors H-8,G-6 --threshold 20000
hawa check-alerts --send
```

Or over HTTP:

```bash
curl -X POST localhost:8000/v1/subscriptions \
  -H 'content-type: application/json' \
  -d '{"channel":"webhook","target":"https://your.app/hook","sectors":"H-8"}'
```

Alerts are deduplicated per subscriber, per sector, per day, so the 30-minute
poll cannot spam anyone. Delivery failures leave the alert retryable rather than
dropping it. Channels ship for Telegram and generic webhooks — the webhook is
how you'd wire up WhatsApp through a provider, or web push.

## Configuration

Secrets and bootstrap values come from the environment (see `.env.example`):

| Variable | Purpose |
|---|---|
| `HAWA_DATABASE_URL` | Defaults to local SQLite |
| `HAWA_TELEGRAM_BOT_TOKEN` | Needed for Telegram alerts |
| `HAWA_PURPLEAIR_API_KEY` | Enables the PurpleAir source |
| `HAWA_OPENAQ_API_KEY` | Enables the OpenAQ source |
| `HAWA_POLL_INTERVAL_MINUTES` | Default 30 |

Everything that changes more often than the code — thresholds, the PMD URL,
which sources are enabled — lives in a database row, not in the source:

```bash
hawa settings show
hawa settings set pollen_alert_threshold 15000
hawa settings set enabled_sources '["pmd_pollen","openaq"]'
```

Changes take effect within a minute. No redeploy. This exists because PMD moving
a URL mid-season should not require a release.

## Contributing

The most useful contributions, roughly in order:

1. **Run PurpleAir or OpenAQ against a real key** and tell us what breaks.
2. **Write a scraper for another source** — EPA Pakistan, IQAir, a university
   sensor network. `src/hawa/sources/base.py` is a small interface and adding
   one touches nothing else.
3. **Build something on the API** — a mobile widget, a Telegram bot with nicer
   formatting, a seasonal chart.
4. **Historical backfill.** We only have data from the day we started polling.
   If you can find archived PMD press releases, that history is worth a lot.

Every source needs a saved response in `tests/fixtures/` and a parser test.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

```bash
pytest              # full suite
ruff check .        # lint
mypy                # types
```

`brain/` holds the project's working notes — start with
[brain/landmines.md](brain/landmines.md) if you're about to change the scraper
or the ingest path.

## A note on scraping politely

This sends an identifying User-Agent naming the project, polls every 30 minutes
by default (PMD publishes once a day — you do not need to poll faster), and
caches identical responses instead of re-storing them. Please do not lower the
interval. The goal is for this data to be more available, not for PMD's server
to have a bad time.

## Licence

MIT. Pollen data is produced by the Pakistan Meteorological Department; air
quality data by OpenAQ (CC BY 4.0) and PurpleAir contributors. Attribution is
returned in every API response.
