// Can a Cloudflare Worker reach PMD -- on an HTTP request, and on a cron?
//
// Why both. weather.gov.pk is itself behind Cloudflare (Server: cloudflare,
// CF-RAY, 104.21.x/172.67.x), so the 403s that datacenters get are Cloudflare's
// bot management blocking datacenter ASNs. A Worker's subrequest to a
// Cloudflare-proxied origin stays inside that network, which is why the
// browser-triggered probe succeeds.
//
// But a Worker runs near its *caller*, and a scheduled Worker has no caller.
// The manual probe returning colo ISB proves only that a request originating in
// Islamabad works -- it says nothing about where a cron fires from. Since the
// whole serverless plan depends on the cron path, that is the case that has to
// be measured, not assumed.
//
// Deploy and watch:
//   npx wrangler deploy
//   npx wrangler tail          # then wait for the next 5-minute tick
//
// The colo is read back from PMD's own CF-RAY header (it is suffixed with the
// colo code), which works in the scheduled handler where request.cf does not
// exist.

const TARGET = "https://weather.gov.pk/rnd/pollen-data";

const AGENTS = {
  hawa: "hawa/0.1 (+https://github.com/abdullahbilal-y/islamabad-air) open-data client",
  browser:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
  none: "",
};

async function probe(userAgent) {
  const started = Date.now();
  try {
    const res = await fetch(TARGET, {
      headers: userAgent ? { "User-Agent": userAgent } : {},
      redirect: "follow",
      // A cached hit would measure Cloudflare's cache rather than PMD's origin,
      // which is the opposite of the point.
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    const body = await res.text();
    const ray = res.headers.get("cf-ray") ?? "";
    return {
      status: res.status,
      bytes: body.length,
      ms: Date.now() - started,
      // CF-RAY looks like "a3684fdf5d601493-ISB"; the suffix is the colo that
      // served it. This is how we learn where a cron actually runs.
      colo: ray.includes("-") ? ray.split("-").pop() : null,
      // PMD renders its table into the page's JavaScript. A 200 without
      // `rows.push` is a block page, so the status code alone proves nothing.
      looks_like_pollen_data: body.includes("rows.push"),
    };
  } catch (err) {
    return { status: null, error: String(err), ms: Date.now() - started };
  }
}

async function runAll() {
  const results = {};
  for (const [name, ua] of Object.entries(AGENTS)) {
    results[name] = await probe(ua);
  }
  const usable = Object.values(results).some(
    (r) => r.status === 200 && r.looks_like_pollen_data,
  );
  return { results, usable };
}

export default {
  async fetch(request) {
    const { results, usable } = await runAll();
    return Response.json(
      {
        platform: "cloudflare-workers",
        trigger: "http",
        // Where this Worker instance ran. For an HTTP trigger this is a POP
        // near the caller, so a good result here does NOT generalise to cron.
        colo: request.cf?.colo ?? null,
        target: TARGET,
        results,
        verdict: usable
          ? "USABLE on the HTTP path. Now check the cron path with `wrangler tail`."
          : "BLOCKED - this network cannot fetch PMD.",
      },
      { headers: { "cache-control": "no-store" } },
    );
  },

  // The measurement that actually decides the architecture.
  async scheduled(event, env, ctx) {
    const { results, usable } = await runAll();
    const colo = Object.values(results).find((r) => r.colo)?.colo ?? "unknown";
    // Surfaced through `wrangler tail`. If this says USABLE, the ingest can run
    // entirely on Cloudflare's free tier and no machine at home is needed.
    console.log(
      JSON.stringify({
        trigger: "cron",
        cron: event.cron,
        served_by_colo: colo,
        verdict: usable ? "USABLE on the cron path" : "BLOCKED on the cron path",
        results,
      }),
    );
  },
};
