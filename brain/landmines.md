# Landmines

Non-obvious traps in this repo. Each entry says **why**, because the why is what
stops the next person re-introducing it.

Items marked *(inherited — verify applies)* came from the cross-project
knowledge base as hypotheses and have not yet been confirmed against this
codebase's real behaviour.

---

## 1. A 200 OK from PMD proves nothing — check that rows came out
**What:** `https://weather.gov.pk/rnd/pollen-data` returns HTTP 200 with a full
HTML page even when our parser can extract nothing from it. "No pollen today"
and "our scraper is broken" are byte-identical from the outside.

**Why:** The pollen table is rendered client-side from a `rows.push({...})`
block inside the page's JavaScript. Any change to that block's shape leaves the
HTTP layer perfectly healthy while the data silently becomes empty. During
paper mulberry season, serving a confident "no pollen" is worse than serving an
error.

**Do:** `parse_pmd_pollen` raises `PollenParseError` on zero rows rather than
returning an empty result, `ingest_source` records that as a `parse`-stage
failure against health, and `/healthz` goes 503. Never "fix" a failing parse by
making the parser return empty.

**Refs:** `src/hawa/parsers/pmd.py`, `src/hawa/ingest.py`,
`tests/test_pmd_parser.py::test_a_200_with_no_rows_is_an_error_not_an_empty_day`

---

## 2. `session_scope()` commits on exit, which **expires** every loaded object
**What:** Reading ORM attributes after a `with session_scope()` block raises
`DetachedInstanceError`. This bit three separate places during initial
development, and in `settings_store` it was invisible: the broad `except`
swallowed it and logged "keeping last known-good", so `settings set` appeared to
succeed and silently never took effect.

**Why:** SQLAlchemy's default `expire_on_commit=True` marks every instance stale
after a commit; a detached instance cannot then refresh itself. The failure mode
is a *lazy* one — the object looks fine until you touch an attribute.

**Do:** Materialise what you need (tuples, dicts, Pydantic models) *inside* the
session block. If a function must return ORM rows after committing, re-query
them in one final `select` — see `alerts/engine.py::evaluate`, which collects ids
during the loop and loads the rows once at the end.

**Refs:** `src/hawa/settings_store.py::_load_from_db`,
`src/hawa/alerts/engine.py::evaluate`, `src/hawa/cli.py::export`,
`tests/test_cli.py`

---

## 3. Yesterday's pollen page cannot be re-fetched — store the bytes first
**What:** `ingest_source` persists the raw response body and **commits** before
it parses anything.

**Why:** PMD publishes one figure per day and does not maintain a queryable
archive. If our parser is broken on the day we fetch, that day is gone forever —
there is no retry that recovers it. Coupling the two so a parse exception rolls
back the fetch sacrifices the irreplaceable thing to protect the replaceable one.

**Do:** Keep the ordering. Parse inside its own try/except that marks the
snapshot and swallows. `hawa reparse` then replays stored snapshots through a
fixed parser and recovers the days you would otherwise have lost — this is the
entire reason `raw_snapshot` exists, and it only works because every write is
idempotent (#4).

**Refs:** `src/hawa/ingest.py`,
`tests/test_ingest.py::test_reparse_recovers_a_day_after_the_parser_is_fixed`

---

## 4. Every write is idempotent on a natural key — do not break this
**What:** `pollen_reading` is unique on (source, observed_date, sector,
pollen_type); `air_reading` on (source, sensor_id, metric, observed_at);
`alert_event` on `dedup_key`.

**Why:** The scheduler polls every 30 minutes, the boot catch-up runs on every
deploy, and `reparse` replays history. Each of those is a double-write bug
without a natural key — and once you have one such bug you become afraid to run
recovery at all, which is how gaps become permanent.

**Do:** Any new source must define its natural key before it writes anything. If
you cannot name one, you do not yet understand the data.

**Refs:** `src/hawa/models.py`, `tests/test_ingest.py::test_running_twice_writes_nothing_new`

---

## 5. A null sector cell is not a zero
**What:** On most days only H-8 reports; E-8, G-6 and F-10 come back as `null`.
The parser skips those rows entirely rather than storing 0.

**Why:** A missing trap reading and a genuine zero pollen count are different
facts. Storing 0 for "not reported" would drag every city-wide average down, and
the error is undetectable afterwards because 0 is a perfectly plausible count.

**Do:** Skip nulls. Absence of a row means "not reported"; a row with `value=0`
means the trap actually reported zero (PMD prints this as `"00"`).

**Refs:** `src/hawa/parsers/pmd.py::_rows_from_objects`,
`tests/test_pmd_parser.py::test_null_cells_are_omitted_not_zeroed`

---

## 6. PMD's hostnames rot — the URL lives in a settings row, not in code
**What:** During development (2026-09-05) `rnd.pmd.gov.pk` and
`namc.pmd.gov.pk` both returned HTTP 500. Only `weather.gov.pk/rnd/pollen-data`
worked. The `pmd_pollen_url` setting is a database row.

**Why:** A government site moving a page mid-season should not require a code
change and a redeploy — that is exactly when you least want a release cycle in
the loop. Config that changes faster than your deploy cadence belongs in data.

**Do:** `hawa settings set pmd_pollen_url '<new url>'`. Takes effect within 60
seconds (the settings cache TTL). Do not hardcode a URL "just for now".

**Refs:** `src/hawa/settings_store.py`, `src/hawa/sources/pmd_pollen.py`

---

## 7. `POST /v1/subscriptions` is unauthenticated
**What:** Anyone who can reach the API can register a webhook target, and the
service will then POST to it.

**Why:** v0 is scoped for local and trusted deployments. On the open internet
this is an SSRF-adjacent abuse vector: point a webhook at a third party and make
this service send traffic on their behalf.

**Do:** Put it behind auth, a rate limit, or a target allowlist before exposing
the API publicly. The read endpoints are safe to expose as they are.

**Refs:** `src/hawa/api/routes.py::create_subscription`

---

## 8. PurpleAir and OpenAQ have never run against a live key
**What:** Both sources are written against their documented APIs and unit-tested
against synthetic payloads. Neither has made a real authenticated call.

**Why:** No API key was available during development. Saying "supports PurpleAir"
without that caveat is exactly the kind of README claim that wastes a
contributor's afternoon.

**Do:** They report `available: false` with a reason when no key is set, so they
fail visibly rather than silently. If you have a key: run them, and fix what
breaks. Confirm or correct this entry afterwards. Expect the response *shape* to
be the thing that is wrong, not the auth.

**Refs:** `src/hawa/sources/purpleair.py`, `src/hawa/sources/openaq.py`

---

## 9. Dates are Pakistan Standard Time, not UTC
**What:** `today_pkt()` is used everywhere a "current date" is needed.

**Why:** PKT is UTC+5. After 19:00 UTC, a naive `date.today()` on a UTC server is
already tomorrow in Islamabad — so the ingest would file the evening's reading
under the wrong day, and the alert dedup key (which contains the date) would
fire a second time for the same reading.

**Do:** Use `hawa.parsers.pmd.today_pkt()`. Note this needs the `tzdata` package
on Windows, which is why it is a hard dependency rather than a dev extra.

**Refs:** `src/hawa/parsers/pmd.py::today_pkt`

---

## 10. FastAPI's TestClient is an `httpx.Client` — do not block it
**What:** The autouse fixture that stops tests making real network calls patches
`httpx.HTTPTransport.handle_request`, not `httpx.Client.send`.

**Why:** `TestClient` is an `httpx.Client` over an in-process ASGI transport.
Patching the client blocks every API test with a confusing "tried to make a real
HTTP request" error even though nothing left the process.

**Refs:** `tests/conftest.py::_no_accidental_network`

---

## 11. *(inherited — verify applies)* A restart does not fix an upstream redesign
**What:** `/healthz` (data health) and `/livez` (process liveness) are separate
endpoints on purpose. Orchestrators should be pointed at `/livez`.

**Why:** If a container's liveness probe is wired to data health, a PMD markup
change puts the service into a restart loop that cannot possibly fix it, and the
restart churn hides the actual signal.

**Status:** The split is implemented; nobody has yet run this under a real
orchestrator to confirm the probe wiring behaves as intended.

**Refs:** `src/hawa/main.py::livez`, `src/hawa/api/routes.py::healthz`

---

## 12. PMD blocks datacenter IPs — this cannot be deployed to a plain cloud host
**What:** `https://weather.gov.pk/rnd/pollen-data` returns **HTTP 403** when
requested from a GitHub Actions runner, while the identical request from a
residential connection returns 200. Confirmed 2026-09-05 on the first CI run
after publishing.

**Why:** The site (or a WAF in front of it) filters on the network, not on the
request. Isolated with a controlled probe (`.github/workflows/egress-probe.yml`,
run 2026-09-05):

| Request | From home | From Actions (Azure eastus, 74.235.126.85) |
|---|---|---|
| our `hawa/0.1` UA | 200 | 403 |
| browser UA | 200 | 403 |
| curl default / no UA | 200 | 403 |

A third network (a US service provider's fetch infrastructure, unrelated to
Azure) also returned 403 on 2026-09-05. All three User-Agents behave identically
from each location, two unrelated datacenters are blocked and a home connection
is not — so the discriminator is the egress IP. It is not
rate limiting either: the very first request of the run is refused, with an
identical 4549-byte WAF block page each time.

**The mechanism, found 2026-09-05:** `weather.gov.pk` is itself behind
Cloudflare. So the block is Cloudflare bot management refusing datacenter ASNs,
not something PMD configured by hand — which is why *every* mainstream cloud is
refused while a home connection is not. It also means a **Cloudflare Worker gets
through**, because its subrequest to a Cloudflare-proxied origin never leaves
that network: measured 200 with real data from colo `ISB`. Whether a *scheduled*
Worker also gets through is a separate question — Workers run near the caller
and a cron has none. Do not treat the HTTP result as covering cron.

**Do:** Re-run the probe from any environment before hosting the ingest there;
do not infer. Two consequences, and neither is a code bug.

1. **Deployment.** A default AWS/GCP/Azure/Fly/Render host will very likely get
   403 forever. Deploy somewhere with a residential or local Pakistani IP, or
   put an egress proxy in front of the fetch. Verify with one `hawa ingest` from
   the target host *before* building anything on top of the deployment.
2. **CI.** The `upstream-check` job treats a 403 as inconclusive and passes.
   A job that fails every single run teaches everyone to ignore it, which
   destroys the one signal it exists to give — a genuine parse break.

Do **not** "fix" this by spoofing a browser User-Agent or rotating IPs. This is
public data we are reading politely; if PMD does not want datacenter traffic,
the answer is to run somewhere else, not to disguise the client.

**Refs:** `.github/workflows/ci.yml` (upstream-check), `src/hawa/sources/base.py::build_client`
