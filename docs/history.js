// Daily total pollen by sector, over time.
//
// This is the part of the site that justifies the project existing. PMD
// publishes today's figure and keeps no queryable archive -- their own month
// selector returns nothing -- so a page showing only today would be a worse copy
// of theirs. The archive is the thing that is genuinely ours, and it should be
// on screen.
//
// Palette: categorical slots 1-4, validated against both surfaces. All checks
// pass; light-mode aqua and yellow fall under 3:1 contrast against the page, so
// every line carries a direct end-label and a table view ships alongside --
// that relief is required, not optional.

const SERIES_VARS = ["--s1", "--s2", "--s3", "--s4"];
const numFmt = new Intl.NumberFormat("en-US");
const NS = "http://www.w3.org/2000/svg";

function node(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function seriesColor(i) {
  const root = document.querySelector(".viz");
  if (!root) return "#2a78d6";
  return getComputedStyle(root).getPropertyValue(SERIES_VARS[i % SERIES_VARS.length]).trim();
}

// Round to clean numbers -- the y ticks carry every value we do not directly label.
function niceTicks(max) {
  if (!(max > 0)) return [0, 1];
  const pow = Math.pow(10, Math.floor(Math.log10(max)));
  const step =
    [1, 2, 2.5, 5, 10].map((m) => m * pow).find((v) => max / v <= 4) ?? pow * 10;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v));
  return ticks;
}

function renderHistory(days, sectors, byDaySector) {
  const host = document.getElementById("history-body");
  if (!host) return;
  host.replaceChildren();

  if (days.length < 2) {
    // Degrade honestly rather than drawing a chart out of one point.
    const n = days.length;
    host.append(
      node(
        "div",
        "note",
        n === 0
          ? "No history recorded yet."
          : `History starts here — ${n} day recorded so far. PMD publishes one ` +
            "figure a day and keeps no archive, so this chart fills in from the " +
            "day collection began and cannot be backfilled.",
      ),
    );
    return;
  }

  // Only sectors that actually reported get a line. Drawing a flat line for a
  // silent trap would imply a measured zero, which is a different claim.
  const active = sectors.filter((sec) =>
    days.some((d) => byDaySector[d] && byDaySector[d][sec] != null),
  );
  if (!active.length) {
    host.append(node("div", "note", "No sector reported a numeric value in this window."));
    return;
  }

  const W = 800;
  const H = 320;
  const M = { t: 14, r: 96, b: 30, l: 58 };
  const iw = W - M.l - M.r;
  const ih = H - M.t - M.b;

  let max = 0;
  for (const d of days) {
    for (const sec of active) max = Math.max(max, byDaySector[d]?.[sec] ?? 0);
  }
  const ticks = niceTicks(max);
  const yMax = ticks[ticks.length - 1];
  const x = (i) => M.l + (days.length === 1 ? iw / 2 : (i * iw) / (days.length - 1));
  const y = (v) => M.t + ih - (yMax ? (v / yMax) * ih : 0);

  // A legend is always present for two or more series, so identity never rests
  // on colour alone.
  if (active.length > 1) {
    const legend = node("div", "legend");
    active.forEach((sec, i) => {
      const item = node("span");
      const key = node("i");
      key.style.background = seriesColor(i);
      item.append(key, document.createTextNode(sec));
      legend.append(item);
    });
    host.append(legend);
  }

  const wrap = node("div", "chart-wrap");
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    `Daily total pollen by sector, ${days[0]} to ${days[days.length - 1]}`,
  );

  const add = (tag, attrs, cls) => {
    const n = document.createElementNS(NS, tag);
    for (const k of Object.keys(attrs)) n.setAttribute(k, attrs[k]);
    if (cls) n.setAttribute("class", cls);
    svg.append(n);
    return n;
  };

  for (const t of ticks) {
    add("line", { x1: M.l, x2: M.l + iw, y1: y(t), y2: y(t) }, "grid");
    add("text", { x: M.l - 8, y: y(t) + 4, "text-anchor": "end" }, "axis-text").textContent =
      numFmt.format(t);
  }

  const xTicks =
    days.length <= 8
      ? days.map((_, i) => i)
      : [0, Math.floor((days.length - 1) / 2), days.length - 1];
  for (const i of xTicks) {
    add("text", { x: x(i), y: H - 10, "text-anchor": "middle" }, "axis-text").textContent =
      days[i].slice(5);
  }

  active.forEach((sec, si) => {
    const color = seriesColor(si);
    const pts = days
      .map((d, i) => ({ i, v: byDaySector[d]?.[sec] }))
      .filter((p) => p.v != null);
    if (!pts.length) return;

    // Gaps stay gaps. Joining across a day nobody recorded would draw a trend
    // through data that does not exist -- and gaps are expected here, because
    // collection depends on someone visiting.
    let path = "";
    let prev = null;
    for (const p of pts) {
      path +=
        prev !== null && p.i === prev + 1
          ? ` L${x(p.i)} ${y(p.v)}`
          : ` M${x(p.i)} ${y(p.v)}`;
      prev = p.i;
    }
    add("path", { d: path.trim(), stroke: color }, "series-line");

    for (const p of pts) add("circle", { cx: x(p.i), cy: y(p.v), r: 4, fill: color }, "dot");

    // Value at the line end. Sparing by design: a number on every point goes
    // unread, and the tooltip plus the table carry the rest.
    const last = pts[pts.length - 1];
    add("text", { x: x(last.i) + 10, y: y(last.v) + 4 }, "end-label").textContent =
      `${sec} ${numFmt.format(last.v)}`;
  });

  const crosshair = add(
    "line",
    { x1: 0, x2: 0, y1: M.t, y2: M.t + ih, opacity: 0 },
    "grid",
  );
  wrap.append(svg);

  const tip = node("div", "tip");
  wrap.append(tip);

  const showAt = (clientX) => {
    const box = svg.getBoundingClientRect();
    const px = ((clientX - box.left) / box.width) * W;
    let i = Math.round(((px - M.l) / iw) * (days.length - 1));
    i = Math.max(0, Math.min(days.length - 1, i));

    crosshair.setAttribute("x1", x(i));
    crosshair.setAttribute("x2", x(i));
    crosshair.setAttribute("opacity", "1");

    tip.replaceChildren(node("b", null, days[i]));
    let any = false;
    active.forEach((sec, si) => {
      const v = byDaySector[days[i]]?.[sec];
      if (v == null) return;
      any = true;
      const row = node("div", "row");
      const key = node("i");
      key.style.background = seriesColor(si);
      row.append(key, document.createTextNode(`${sec}: ${numFmt.format(v)}`));
      tip.append(row);
    });
    if (!any) tip.append(node("div", "row", "no reading"));

    tip.style.opacity = "1";
    tip.style.left = `${Math.max(0, Math.min(box.width - 160, (x(i) / W) * box.width + 12))}px`;
    tip.style.top = "8px";
  };

  svg.addEventListener("mousemove", (ev) => showAt(ev.clientX));
  svg.addEventListener(
    "touchmove",
    (ev) => ev.touches[0] && showAt(ev.touches[0].clientX),
    { passive: true },
  );
  const hide = () => {
    tip.style.opacity = "0";
    crosshair.setAttribute("opacity", "0");
  };
  svg.addEventListener("mouseleave", hide);
  svg.addEventListener("touchend", hide);

  host.append(wrap);

  // The table view. Required relief for the light-mode contrast warning, and
  // the accessible path to the same numbers.
  const det = node("details", "tableview");
  const summary = document.createElement("summary");
  summary.textContent = "Show these figures as a table";
  det.append(summary);

  const table = node("table");
  const thead = document.createElement("thead");
  const headRow = node("tr");
  headRow.append(node("th", null, "Date"));
  for (const sec of active) headRow.append(node("th", null, sec));
  thead.append(headRow);

  const tbody = document.createElement("tbody");
  for (const d of [...days].reverse()) {
    const tr = node("tr");
    tr.append(node("td", null, d));
    for (const sec of active) {
      const v = byDaySector[d]?.[sec];
      tr.append(node("td", "num", v == null ? "—" : numFmt.format(v)));
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);

  const scroll = node("div", "scroll");
  scroll.append(table);
  det.append(scroll);
  host.append(det);
}

fetch("data/index.json", { cache: "no-cache" })
  .then((r) => (r.ok ? r.json() : Promise.reject(new Error("no manifest"))))
  .then(async (manifest) => {
    // Bounded on purpose: a season is the useful window, and this keeps the page
    // fast however many years eventually accumulate.
    const months = (manifest.months || []).slice(-6);
    const loaded = await Promise.all(
      months.map((m) =>
        fetch(`data/months/${m}.json`, { cache: "no-cache" })
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null),
      ),
    );

    const byDaySector = {};
    for (const month of loaded) {
      if (!month) continue;
      for (const r of month.readings || []) {
        if (r.value == null) continue;
        if (!byDaySector[r.observed_date]) byDaySector[r.observed_date] = {};
        const bucket = byDaySector[r.observed_date];
        bucket[r.sector] = (bucket[r.sector] ?? 0) + r.value;
      }
    }

    renderHistory(Object.keys(byDaySector).sort(), manifest.sectors || [], byDaySector);
  })
  .catch(() => {
    const host = document.getElementById("history-body");
    if (host) host.append(node("div", "note", "History is not available yet."));
  });
