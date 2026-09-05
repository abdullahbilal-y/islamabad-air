# Hawa — Islamabad Pollen Data

**An open, queryable archive of Islamabad's daily pollen counts — and something that actually tells you when they spike.**

📊 **[Live data and dashboard](https://abdullahbilal-y.github.io/islamabad-air/)** ·
🔌 [`latest.json`](https://abdullahbilal-y.github.io/islamabad-air/data/latest.json) ·
📈 [Full CSV](https://abdullahbilal-y.github.io/islamabad-air/data/pollen.csv)

---

## What this is, and what it isn't

The Pakistan Meteorological Department measures pollen every day in sectors
H-8, E-8, G-6 and F-10, and publishes it at
[weather.gov.pk/rnd/pollen-data](https://weather.gov.pk/rnd/pollen-data).
**Their page is good. If you just want today's number, go there** — this project
does not try to replace it.

Three things it doesn't do, which is where this comes in:

- **It won't tell you anything.** You have to remember to check. For someone
  whose spring is ruined by paper mulberry, "a message arrives when H-8 crosses
  15,000" is the whole point, and no dashboard substitutes for it.
- **There's no history.** Their month selector returns no rows — even PMD won't
  hand you last April. Anything that starts accumulating today owns a dataset
  that otherwise won't exist.
- **It isn't machine-readable.** The table is rendered in JavaScript, so nobody
  can build on it.

So: **an alert engine, a growing archive, and a real API.** The dashboard here
exists to make the data legible, not to compete with PMD's.

## The API is just files

No key, no rate limit, no server that can go down. About 32 rows a day means a
year of this dataset is roughly one megabyte — at that size a JSON file on a CDN
beats a hosted API on every axis that matters, including staying alive.

```bash
curl https://abdullahbilal-y.github.io/islamabad-air/data/latest.json
curl https://abdullahbilal-y.github.io/islamabad-air/data/months/2026-09.json
curl https://abdullahbilal-y.github.io/islamabad-air/data/pollen.csv
```

| File | Contents |
|---|---|
| `data/latest.json` | Newest day: every reading, plus per-sector totals and severity bands |
| `data/months/YYYY-MM.json` | One month of readings with daily summaries |
| `data/pollen.csv` | The entire history, one row per reading |
| `data/index.json` | Manifest — date range, months available, sectors, pollen types |

```json
{
  "observed_date": "2026-09-04",
  "sectors": [
    { "sector": "H-8", "total": 76, "band": "absent",
      "top_type": "Cannabis", "top_value": 36, "types_reported": 8 }
  ]
}
```

Every reading carries both `value` (parsed) and `value_raw` (exactly what PMD
printed — `"00"`, not `0`). If a cell couldn't be read, `value` is `null`,
`value_raw` keeps the text, and `needs_review` is `true`. We never guess a
number, because a plausible wrong number is one nobody ever checks.

## How it collects data without a server

PMD serves requests from Pakistan and refuses them everywhere else. Measured
across five networks:

| Origin | Result |
|---|---|
| Islamabad home connection | **200** |
| Cloudflare Worker, `ISB` colo | **200** |
| Cloudflare Worker, `FRA` colo | **403** |
| GitHub Actions (Azure `eastus`) | **403** |
| US service network | **403** |

`weather.gov.pk` is behind Cloudflare, so this is almost certainly a country
rule in their WAF. Either way the operative constraint is: **the fetch has to
originate in Pakistan.** Nothing else does.

So exactly one step is placed there, and the rest runs on free infrastructure:

```
someone in Pakistan opens the dashboard
            │
            ▼
  Cloudflare Worker (runs in the ISB colo)
  fetches PMD ──► caches the page in KV
            │
            ▼
  GitHub Actions, 3×/day
  pulls the cached page (a workers.dev request — location irrelevant)
            │
            ▼
  the Python parser ──► docs/data/*.json ──► committed ──► GitHub Pages
```

The Worker (`worker/`) deliberately does **not** parse. There is one parser, in
Python, tested against a real captured page; a second implementation in
JavaScript would drift from it silently and nobody would notice until the
numbers disagreed. It also holds no credentials and never writes to GitHub — CI
pulls from it. A public endpoint carrying a repo-scoped token is a much worse
thing to operate than one that can only hand out a copy of a public web page.

It refuses to cache a body without `rows.push` in it, because a 403 block page
is still a body, and caching one would make staleness invisible.

**The honest weakness:** freshness depends on someone in Pakistan opening the
page. Nobody looks for a week, the archive has a week-long gap, and PMD keeps no
archive to backfill from. The dashboard shows a staleness badge rather than
letting an old reading pass as current. One bookmark tap on a phone also does
it — no laptop, no Python.

### Running the collection yourself

For a machine in Pakistan that can fetch PMD directly, skipping the relay:

```bash
git clone https://github.com/abdullahbilal-y/islamabad-air
cd islamabad-air
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

hawa poll        # fetch, evaluate alerts, republish docs/data
```

On a schedule:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1   # Windows
```

```bash
0 10,19 * * * /path/to/islamabad-air/scripts/daily-poll.sh >> /tmp/hawa.log 2>&1
```

And to process a page someone else fetched:

```bash
hawa ingest-file snapshot.html --source pmd_pollen --fetched-at 2026-09-05T12:00:00Z
```

Please don't work around a 403 by spoofing a browser or rotating IPs. This is
public data read politely, a few times a day, with an identifying User-Agent.
`probe/` holds deployable probes if you want to measure another host.

## Alerts

```bash
hawa subscribe telegram 123456789 --sectors H-8,G-6 --threshold 15000
hawa poll     # evaluates and delivers on every run
```

Deduplicated per subscriber, per sector, per day, so a twice-daily poll can't
spam anyone. A delivery failure leaves the alert retryable rather than dropping
it. Telegram and generic webhooks ship; the webhook is how you'd wire up
WhatsApp through a provider, or web push.

Subscriptions live only in your local database and are **never** published —
they contain chat ids and webhook URLs, and there's a test asserting they can't
leak into the repo.

## Design notes

Scraping a page you have no agreement with is a specific problem, and most of
this codebase is a response to it.

**The raw bytes are stored before anything parses them.** Today's count exists
for one day; if the parser is broken when we fetch, that number is gone forever.
So ingest persists the body and commits, *then* parses separately. Fix the
parser later and `hawa reparse` replays stored snapshots to recover the days.

**A 200 OK with zero rows is a failure, not an empty day.** When PMD redesigns
the page the fetch still succeeds and the parser silently finds nothing. "No
pollen today" and "our scraper broke" are indistinguishable from outside, and
only one is safe to serve in March. So the parser raises.

**Every write is idempotent on a natural key.** Which is what makes retries, the
catch-up run, and `reparse` safe to do aggressively.

**Health is measured on outcomes.** `hawa health` tracks whether fetches
actually produce rows *and* how old the newest reading is — a green process
serving three-week-old counts is exactly the failure worth catching.

## Also included

A full FastAPI service (`hawa serve`) with OpenAPI docs and a live dashboard.
It needs a host, so it isn't what's deployed — but it's useful in development
and it's there if you ever want a dynamic API.

## Contributing

Most useful first, roughly:

1. **Run PurpleAir or OpenAQ against a real key.** Both are written to their
   documented APIs but have never made a live authenticated call. They report
   themselves unavailable without a key rather than pretending to work.
2. **A source for somewhere else.** `src/hawa/sources/base.py` is a small
   interface; adding one touches nothing else.
3. **Anything built on the data.** A bot, a widget, a seasonal chart.
4. **Historical backfill.** We only hold data from the day polling started. If
   you can find archived PMD releases, that history is worth a lot.

Every source needs a real captured response in `tests/fixtures/` and a parser
test. See [CONTRIBUTING.md](CONTRIBUTING.md), and read
[brain/landmines.md](brain/landmines.md) before touching the scraper.

```bash
pytest && ruff check . && mypy
```

## Licence

MIT. Pollen data is produced by the Pakistan Meteorological Department;
attribution ships in every published file. Not affiliated with or endorsed by
PMD, and not medical advice.
