# Egress probes — can this host reach PMD?

PMD returns **HTTP 403 to datacenter IPs**. That single fact decides where the
ingest can run, so this directory lets you measure it on any platform instead of
trusting a claim in a README.

Each probe sends the *same three requests* — our `hawa/0.1` User-Agent, a browser
User-Agent, and no User-Agent — and reports the status code, the response size,
whether the body actually contains pollen data, and the outbound IP. Sending all
three is the point: if they all return the same code, the User-Agent is not the
variable and the network is.

## What's known so far

| Environment | Result | Measured |
|---|---|---|
| Home connection (Islamabad) | **200**, all three UAs | 2026-09-05 |
| GitHub Actions (Azure, `eastus`) | **403**, all three UAs | 2026-09-05 |
| Anthropic fetch service (US datacenter) | **403** | 2026-09-05 |
| Vercel Functions (AWS Lambda) | not yet measured | — |
| Netlify Functions (AWS Lambda) | not yet measured | — |
| Cloudflare Workers, HTTP trigger, colo `ISB` | **200**, all three UAs, real data | 2026-09-05 |
| Cloudflare Workers, cron trigger, colo `FRA` | **403**, all three UAs | 2026-09-06 |

Both Vercel and Netlify run functions on AWS Lambda, so the expectation is 403 —
but that is an inference, and inference is what these probes exist to replace.

**It is geography, not datacenter.** An earlier draft of this file claimed the
block was datacenter-ASN filtering, and that a Cloudflare Worker got through
because its subrequest to a Cloudflare-proxied origin never leaves that network.
The cron measurement disproves it: a Worker in `FRA` is just as much "inside
Cloudflare" as one in `ISB`, and `FRA` is refused.

Every measurement fits one rule instead — **requests from Pakistan are served,
requests from elsewhere are refused**:

- Islamabad home connection → 200
- Cloudflare Worker, `ISB` colo → 200
- Cloudflare Worker, `FRA` colo → 403
- Azure `eastus`, US service network → 403

`weather.gov.pk` is behind Cloudflare (`Server: cloudflare`, `CF-RAY`, IPs in
`104.21.x`/`172.67.x`), so this is most likely a country rule in their WAF. What
this does *not* separate is geo-blocking from datacenter-blocking outside
Pakistan, since we have no non-Pakistani residential test. Either way the
operative constraint is the same: **the fetch has to originate in Pakistan.**

**This is why the HTTP probe alone would have misled us.** A Worker runs in a
POP near its *caller*, so the successful probe returned `ISB` only because it was
called from Islamabad. A scheduled Worker has no caller and Cloudflare put it in
Frankfurt. Had we shipped on the HTTP result, the ingest would have failed the
first time it ran unattended — the exact failure this repo is built to make
loud rather than silent.

The worker's `scheduled` handler logs the serving colo and verdict, read back
from PMD's own `CF-RAY` header (`request.cf` does not exist in a cron):

```bash
cd probe/cloudflare
npx wrangler kv namespace create PROBE   # then put the id in wrangler.toml
npx wrangler deploy
curl https://<your-worker>.workers.dev/last-cron
```

`/last-cron` returns the most recent scheduled run plus a history of the colos
it fired from. It exists because `wrangler tail` buffers when it is not attached
to a terminal, and because one sample cannot tell "cron always runs in Frankfurt"
from "cron runs wherever it likes and we got unlucky once" — two answers that
imply completely different architectures.

The `kv_namespaces` id in `wrangler.toml` is this project's namespace, not a
secret, but it is account-scoped: create your own with the command above.

Delete the `[triggers]` block once you have the answer.

**If you run one, please open a PR updating this table.** It is genuinely useful
to the next person.

## Vercel

Free Hobby tier, no card required.

```bash
npm i -g vercel
cd probe/vercel
vercel deploy --prod
```

Then open `https://<your-deployment>.vercel.app/api/probe`.

Or via the dashboard: import the repo, set **Root Directory** to `probe/vercel`.

## Netlify

Free tier, no card required.

```bash
npm i -g netlify-cli
cd probe/netlify
netlify deploy --prod
```

Then open `https://<your-site>.netlify.app/.netlify/functions/probe`.

Or via the dashboard: import the repo, set **Base directory** to `probe/netlify`.

## Cloudflare Workers

Free tier, no card required.

```bash
cd probe/cloudflare
npx wrangler deploy
```

Then open the `*.workers.dev` URL it prints.

## GitHub Actions

Already wired up — no deploy needed:

```bash
gh workflow run egress-probe.yml
gh run view --log
```

## Reading the result

```json
{
  "egress_ip": "…",
  "results": {
    "hawa":    { "status": 403, "looks_like_pollen_data": false },
    "browser": { "status": 403, "looks_like_pollen_data": false },
    "none":    { "status": 403, "looks_like_pollen_data": false }
  },
  "verdict": "BLOCKED - this network cannot fetch PMD. Run the ingest somewhere else."
}
```

`looks_like_pollen_data` matters as much as the status code. PMD renders its
table into the page's JavaScript, so a 200 whose body has no `rows.push` is a
block page or a maintenance notice — not data. A host is only usable if it
returns **200 with that flag true**.

## If a host comes back USABLE

Then the ingest could run there, and the architecture could change: a scheduled
function fetches PMD and commits the JSON to this repo through the GitHub API,
removing the need for a machine at home. Vercel Cron and Netlify Scheduled
Functions both cover the once-a-day trigger on their free tiers, and no database
is needed because the repo is the store.

Note that even then, a serverless host has no persistent disk, so the local
SQLite store and its raw snapshots would not survive between runs — the
committed JSON becomes the only archive. That is a real tradeoff, not a
free upgrade.

## Please don't defeat the block

If a host returns 403, the fix is to run somewhere else — not to spoof a browser,
rotate IPs, or route through a residential proxy. This is public data read
politely, twice a day, with an identifying User-Agent that says who we are and
how to reach us. That posture is worth more than the convenience of free hosting.
