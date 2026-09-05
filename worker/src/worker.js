// PMD relay: the one step that must happen inside Pakistan.
//
// The constraint
// --------------
// weather.gov.pk is behind Cloudflare and serves requests from Pakistan while
// refusing them from everywhere else. Measured: an Islamabad home connection and
// a Worker in the ISB colo both get 200; a Worker in FRA, GitHub Actions on
// Azure, and a US service network all get 403. See probe/README.md.
//
// A Cloudflare Worker runs in a POP near its caller. So when someone in Pakistan
// hits this Worker, it executes in ISB and *can* fetch PMD. That is the only
// moving part that has to be there -- parsing, publishing and committing are
// ordinary local work that can happen anywhere, and do, in CI.
//
// What this deliberately does not do
// ----------------------------------
// It does not parse. There is one parser, in Python, with tests against a real
// captured page; a second implementation here would drift from it silently and
// we would not find out until the numbers disagreed. This Worker only moves
// bytes.
//
// It also holds no credentials. It never writes to GitHub -- CI pulls from it.
// A public endpoint with a repo-scoped token on it is a much worse thing to
// operate than one that can only ever hand out a copy of a public web page.

const TARGET = "https://weather.gov.pk/rnd/pollen-data";
const UA =
  "hawa/0.1 (+https://github.com/abdullahbilal-y/islamabad-air) open-data client; contact via GitHub issues";

// PMD publishes once a day. This bounds how often we will touch their server no
// matter how much traffic the dashboard gets -- the refresh endpoint is public,
// so without it any visitor could turn the page into a load generator.
const MIN_REFRESH_MS = 3 * 60 * 60 * 1000;

const json = (body, status = 200) =>
  Response.json(body, {
    status,
    headers: {
      "cache-control": "no-store",
      // The dashboard is on github.io and calls /refresh from the browser.
      "access-control-allow-origin": "*",
    },
  });

async function readMeta(env) {
  const raw = await env.CACHE.get("meta");
  return raw ? JSON.parse(raw) : null;
}

async function refresh(env, force = false) {
  const meta = await readMeta(env);
  const now = Date.now();

  if (!force && meta?.last_attempt_ms && now - meta.last_attempt_ms < MIN_REFRESH_MS) {
    return {
      refreshed: false,
      reason: "rate-limited",
      minutes_until_next: Math.ceil((MIN_REFRESH_MS - (now - meta.last_attempt_ms)) / 60000),
      meta,
    };
  }

  let res;
  try {
    res = await fetch(TARGET, {
      headers: { "User-Agent": UA },
      redirect: "follow",
      // Never serve a cached copy back as if it were a fresh fetch.
      cf: { cacheTtl: 0, cacheEverything: false },
    });
  } catch (err) {
    await env.CACHE.put(
      "meta",
      JSON.stringify({ ...(meta ?? {}), last_attempt_ms: now, last_error: String(err) }),
    );
    return { refreshed: false, reason: "fetch-failed", error: String(err), meta };
  }

  const body = await res.text();
  const ray = res.headers.get("cf-ray") ?? "";
  const colo = ray.includes("-") ? ray.split("-").pop() : null;

  // The critical check. A 403 block page is still a 200-shaped response body,
  // and PMD renders its table into the page's JavaScript -- so `rows.push` is
  // what distinguishes real data from a block page or a maintenance notice.
  // Overwriting a good snapshot with a block page would be worse than not
  // refreshing at all, because the staleness would then be invisible.
  const looksLikeData = res.status === 200 && body.includes("rows.push");

  const nextMeta = {
    ...(meta ?? {}),
    last_attempt_ms: now,
    last_attempt_iso: new Date(now).toISOString(),
    last_status: res.status,
    last_colo: colo,
    last_error: looksLikeData ? null : `status ${res.status}, no pollen rows in body`,
  };

  if (!looksLikeData) {
    await env.CACHE.put("meta", JSON.stringify(nextMeta));
    return {
      refreshed: false,
      reason: res.status === 403 ? "blocked-from-this-colo" : "not-pollen-data",
      status: res.status,
      colo,
      meta: nextMeta,
    };
  }

  nextMeta.fetched_at_iso = new Date(now).toISOString();
  nextMeta.bytes = body.length;
  nextMeta.fetched_from_colo = colo;

  await env.CACHE.put("snapshot", body);
  await env.CACHE.put("meta", JSON.stringify(nextMeta));

  return { refreshed: true, bytes: body.length, colo, meta: nextMeta };
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET, OPTIONS",
        },
      });
    }

    // Called by the dashboard on load, or by a bookmark. Only actually reaches
    // PMD when the caller's POP can, and only every few hours.
    if (pathname === "/refresh") {
      const country = request.cf?.country ?? null;
      const colo = request.cf?.colo ?? null;

      // Save PMD a pointless request from a POP we already know is refused.
      // Not a security control -- just politeness plus a faster answer.
      if (country && country !== "PK") {
        const meta = await readMeta(env);
        return json({
          refreshed: false,
          reason: "caller-outside-pakistan",
          detail:
            "PMD serves requests from Pakistan only. Someone in Pakistan opening " +
            "this page will refresh it.",
          your_country: country,
          your_colo: colo,
          meta,
        });
      }

      return json(await refresh(env));
    }

    // What CI collects. Serving from KV means the caller's location is
    // irrelevant -- GitHub Actions cannot reach PMD, but it can reach this.
    if (pathname === "/snapshot") {
      const [body, meta] = await Promise.all([env.CACHE.get("snapshot"), readMeta(env)]);
      if (!body) {
        return json({ error: "no snapshot cached yet" }, 503);
      }
      return new Response(body, {
        headers: {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-store",
          "access-control-allow-origin": "*",
          // CI reads this to date the reading correctly. Using CI's own clock
          // would misfile a delayed snapshot under the wrong day.
          "x-fetched-at": meta?.fetched_at_iso ?? "",
          "x-fetched-colo": meta?.fetched_from_colo ?? "",
        },
      });
    }

    if (pathname === "/status" || pathname === "/") {
      const meta = await readMeta(env);
      const ageMs = meta?.fetched_at_iso
        ? Date.now() - Date.parse(meta.fetched_at_iso)
        : null;
      return json({
        service: "hawa PMD relay",
        purpose:
          "Fetches PMD from inside Pakistan and caches it, so CI (which cannot " +
          "reach PMD) can collect it. Holds no credentials and never writes.",
        has_snapshot: Boolean(meta?.fetched_at_iso),
        snapshot_age_hours: ageMs === null ? null : +(ageMs / 3600000).toFixed(2),
        your_country: request.cf?.country ?? null,
        your_colo: request.cf?.colo ?? null,
        meta,
        endpoints: {
          "/refresh": "fetch PMD if you are in Pakistan and the cache is stale",
          "/snapshot": "the cached page (what CI collects)",
          "/status": "this",
        },
        repository: "https://github.com/abdullahbilal-y/islamabad-air",
      });
    }

    return json({ error: "not found" }, 404);
  },
};
