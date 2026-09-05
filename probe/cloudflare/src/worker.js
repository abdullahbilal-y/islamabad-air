// Can a Cloudflare Worker reach PMD?
//
// This is the free platform most worth measuring, because it is the one whose
// network genuinely differs from the others. Vercel and Netlify both run on AWS
// Lambda, so they are two tests of roughly the same question; Cloudflare runs on
// its own edge network, from a POP near the visitor rather than from us-east-1.
// A WAF that blocks "cloud provider ranges" does not necessarily treat
// Cloudflare's the same way — and if PMD sits behind Cloudflare themselves, the
// traffic may not look like datacenter egress at all.
//
// That is a hypothesis, not a claim. This measures it.
//
// Deploy (free tier, no card):
//   cd probe/cloudflare && npx wrangler deploy
// Then open the printed *.workers.dev URL.

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
      // Cloudflare caches subrequests by default; a cached hit would measure the
      // cache instead of the origin, which is the opposite of the point.
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    const body = await res.text();
    return {
      status: res.status,
      bytes: body.length,
      ms: Date.now() - started,
      // PMD renders its table into the page's JavaScript. A 200 without
      // `rows.push` is a block page or a maintenance notice, not data — so the
      // status code alone is not enough to call this a success.
      looks_like_pollen_data: body.includes("rows.push"),
    };
  } catch (err) {
    return { status: null, error: String(err), ms: Date.now() - started };
  }
}

export default {
  async fetch(request) {
    const results = {};
    for (const [name, ua] of Object.entries(AGENTS)) {
      results[name] = await probe(ua);
    }

    const usable = Object.values(results).some(
      (r) => r.status === 200 && r.looks_like_pollen_data,
    );

    return Response.json(
      {
        platform: "cloudflare-workers",
        // Which POP served this. Worth recording: the answer may differ by
        // region, and a Worker runs near the caller, not in one fixed place.
        colo: request.cf?.colo ?? null,
        target: TARGET,
        results,
        verdict: usable
          ? "USABLE - this network can reach PMD, so the ingest could run here."
          : "BLOCKED - this network cannot fetch PMD. Run the ingest somewhere else.",
      },
      { headers: { "cache-control": "no-store" } },
    );
  },
};
