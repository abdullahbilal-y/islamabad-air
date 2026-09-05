// Can a Vercel Function reach PMD, or does it get the datacenter 403?
//
// Deploy this folder to Vercel (free Hobby tier), open /api/probe, and read the
// JSON. It answers the only question that decides whether the ingest can be
// hosted serverless: does *this network* get a 200 from weather.gov.pk.
//
// It sends the same three User-Agents the local probe does, because the point is
// to isolate network from request. If all three return the same code, the UA is
// not the variable.

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
    });
    const body = await res.text();
    return {
      status: res.status,
      bytes: body.length,
      ms: Date.now() - started,
      // The signature of a real success: PMD's rows are rendered into the page's
      // JavaScript, so a 200 that lacks them is a block page, not data.
      looks_like_pollen_data: body.includes("rows.push"),
    };
  } catch (err) {
    return { status: null, error: String(err), ms: Date.now() - started };
  }
}

export default async function handler(req, res) {
  const results = {};
  for (const [name, ua] of Object.entries(AGENTS)) {
    results[name] = await probe(ua);
  }

  let egressIp = null;
  try {
    egressIp = (await (await fetch("https://api.ipify.org")).text()).trim();
  } catch {
    // Not essential; the status codes are the answer.
  }

  const usable = Object.values(results).some(
    (r) => r.status === 200 && r.looks_like_pollen_data,
  );

  res.status(200).json({
    platform: "vercel",
    region: process.env.VERCEL_REGION ?? null,
    egress_ip: egressIp,
    target: TARGET,
    results,
    verdict: usable
      ? "USABLE - this network can reach PMD, so the ingest could run here."
      : "BLOCKED - this network cannot fetch PMD. Run the ingest somewhere else.",
  });
}
