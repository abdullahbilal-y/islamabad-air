# System overview

## The problem

Islamabad has severe seasonal pollen (paper mulberry, spring) and severe winter
smog. PMD genuinely measures the pollen — daily, per sector, for H-8, E-8, G-6
and F-10 — and publishes it. But the page renders its table client-side in
JavaScript and keeps no queryable archive, so the numbers are effectively
unavailable: you cannot fetch yesterday's, chart a season, or build an alert.

## What this does

1. **Ingest.** Every 30 minutes, fetch each enabled source. Store the raw
   response body first and commit; then parse; then write observations under a
   natural key so nothing can duplicate.
2. **Serve.** A REST API (`/v1/...`) with OpenAPI docs, plus a small
   server-rendered dashboard at `/`.
3. **Alert.** After each ingest, compare per-sector totals and PM2.5 against
   thresholds, record deduplicated alert events, and deliver them over Telegram
   or a webhook.

## Who uses it

- People with pollen allergies or asthma in Islamabad, via whatever a developer
  builds on top (bot, widget, site).
- Researchers, via `hawa export` — the whole history as one CSV.
- Developers, via the API.

## End-to-end flow

```
PMD page  ─┐
PurpleAir ─┼─► fetch ─► RawSnapshot (committed) ─► parse ─► upsert readings
OpenAQ    ─┘                   │                     │
                               │                     └─► FetchOutcome (health)
                               └─► replayable by `hawa reparse`
                                                     │
                          evaluate thresholds ◄──────┘
                                   │
                          AlertEvent (dedup key)
                                   │
                          Telegram / webhook
```

## Non-goals for v0

- No historical backfill before the day polling started (PMD publishes no archive).
- No auth (see landmines #7).
- No forecasting or health advice — this reports measurements, nothing more.
