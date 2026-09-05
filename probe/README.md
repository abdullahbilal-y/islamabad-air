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
| Vercel Functions (AWS Lambda) | not yet measured | — |
| Netlify Functions (AWS Lambda) | not yet measured | — |
| Cloudflare Workers (CF edge) | not yet measured | — |

Both Vercel and Netlify run functions on AWS Lambda, so the expectation is 403 —
but that is an inference, and inference is what these probes exist to replace.

**Try Cloudflare first.** Vercel and Netlify are two tests of nearly the same
question, since both are AWS Lambda underneath. Cloudflare Workers run on a
different network entirely, from a POP near the caller rather than us-east-1, so
it is the one with a genuinely different chance of succeeding. It is also the
fastest to deploy.

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
