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
| Cloudflare Workers, HTTP trigger (colo `ISB`) | **200**, all three UAs, real data | 2026-09-05 |
| Cloudflare Workers, **cron** trigger | not yet measured — the one that matters | — |

Both Vercel and Netlify run functions on AWS Lambda, so the expectation is 403 —
but that is an inference, and inference is what these probes exist to replace.

**Cloudflare works — and now we know why.** `weather.gov.pk` is itself behind
Cloudflare (`Server: cloudflare`, `CF-RAY`, IPs in `104.21.x`/`172.67.x`), so the
403s handed to AWS and Azure are Cloudflare's own bot management blocking
datacenter ASNs. A Worker's subrequest to a Cloudflare-proxied origin stays
inside that network, so it is not treated as datacenter egress at all.

**But do not stop at the HTTP probe.** A Worker runs in a POP near its *caller*,
and the successful probe returned colo `ISB` because it was called from
Islamabad. A **scheduled** Worker has no caller. Whether cron fires from a colo
that also gets through is a separate question, and the entire serverless plan
depends on it — so the worker now has a `scheduled` handler that logs the colo
and verdict, read back from PMD's own `CF-RAY` header:

```bash
cd probe/cloudflare
npx wrangler deploy
npx wrangler tail        # wait for the next 5-minute tick
```

Delete the `[triggers]` block from `wrangler.toml` once you have the answer.

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
