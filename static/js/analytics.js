/* ProductLab Jobs — analytics: a data-analyst market report + data-health audit. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const api = (p) => fetch(p).then(r => r.json());
  const esc = (s) => (s || "").toString().replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const n = (x) => (x || 0).toLocaleString();

  const PAL = ["var(--jade)", "var(--sky)", "var(--violet)", "var(--signal)", "var(--mint)", "var(--evergreen)"];
  const FRESH_PAL = ["var(--jade)", "var(--sky)", "var(--violet)", "var(--signal)"];
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtWeek = (iso) => { const d = new Date(iso + "T00:00:00"); return isNaN(d) ? iso : MON[d.getMonth()] + " " + d.getDate(); };

  // ── small components ─────────────────────────────────────────────────────
  function deltaPill(delta) {
    if (delta == null) return `<span class="statcard__delta flat">new</span>`;
    const up = delta >= 0, flat = delta === 0;
    return `<span class="statcard__delta ${flat ? "flat" : (up ? "up" : "down")}">${flat ? "±" : (up ? "▲" : "▼")} ${Math.abs(delta)}%</span>`;
  }

  const kpi = (k, v, sub, delta, accent) =>
    `<div class="statcard"><div class="statcard__k">${esc(k)}</div>
       <div class="statcard__v ${accent ? "accent" : ""}">${v}</div>
       ${delta !== undefined ? deltaPill(delta) : ""}
       ${sub ? `<div class="statcard__sub">${esc(sub)}</div>` : ""}</div>`;

  // horizontal ranked bars — sources, companies, seniority
  function rankBars(rows, valueKey, opts = {}) {
    if (!rows || !rows.length) return `<div class="empty">No data yet.</div>`;
    const hi = Math.max(1, ...rows.map(r => r[valueKey]));
    return `<div class="barset">` + rows.map(r => `
      <div class="bar">
        <div class="bar__lbl">${esc(r.label || "—")}${(opts.chip && r.new7) ? `<span class="new-chip">+${n(r.new7)} new</span>` : ""}</div>
        <div class="bar__track"><div class="bar__fill" style="width:${Math.max(2, r[valueKey] / hi * 100)}%"></div></div>
        <div class="bar__val">${n(r[valueKey])}${(opts.pct && r.share != null) ? ` <span class="bar__pct">${r.share}%</span>` : ""}</div>
      </div>`).join("") + `</div>`;
  }

  // segmented mix bar + legend — work mode, freshness
  function segbar(rows, pal) {
    const tot = rows.reduce((s, r) => s + r.value, 0) || 1;
    const seg = rows.map((r, i) => r.value
      ? `<div class="segbar__part" style="width:${(r.value / tot * 100).toFixed(2)}%;background:${pal[i % pal.length]}" title="${esc(r.label)}: ${n(r.value)}"></div>`
      : "").join("");
    const leg = rows.map((r, i) =>
      `<div class="seg-legend__i"><span class="seg-dot" style="background:${pal[i % pal.length]}"></span>${esc(r.label)}
        <b>${Math.round(r.value / tot * 100)}%</b><span class="seg-legend__n">${n(r.value)}</span></div>`).join("");
    return `<div class="segbar">${seg}</div><div class="seg-legend">${leg}</div>`;
  }

  // weekly intake line + area
  function trendChart(rows) {
    const W = 640, H = 180, PX = 10, PT = 14, PB = 26, N = rows.length;
    const max = Math.max(1, ...rows.map(r => r.count));
    const innerW = W - PX * 2, innerH = H - PT - PB, base = PT + innerH;
    const x = (i) => PX + (N <= 1 ? innerW / 2 : i / (N - 1) * innerW);
    const y = (v) => PT + innerH - (v / max) * innerH;
    const pts = rows.map((r, i) => [x(i), y(r.count)]);
    const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const area = `M${pts[0][0].toFixed(1)} ${base} ` + pts.map(p => "L" + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ") + ` L${pts[N - 1][0].toFixed(1)} ${base} Z`;
    const dots = pts.map((p, i) => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${i === N - 1 ? 4 : 2.3}" fill="var(--jade)"/>`).join("");
    return `<svg class="trend-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly intake trend">
      <defs><linearGradient id="tg" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="var(--jade)" stop-opacity="0.22"/>
        <stop offset="1" stop-color="var(--jade)" stop-opacity="0"/></linearGradient></defs>
      <line x1="${PX}" x2="${W - PX}" y1="${base}" y2="${base}" stroke="var(--rule)"/>
      <path d="${area}" fill="url(#tg)"/>
      <path d="${line}" fill="none" stroke="var(--jade)" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>
      ${dots}
      <text x="${PX}" y="${PT + 2}" font-size="11" fill="var(--stone)">${max}</text>
      <text x="${PX}" y="${H - 7}" font-size="11" fill="var(--stone)">${fmtWeek(rows[0].week)}</text>
      <text x="${W - PX}" y="${H - 7}" font-size="11" fill="var(--stone)" text-anchor="end">${fmtWeek(rows[N - 1].week)}</text>
    </svg>`;
  }

  function trendCaption(rows) {
    const N = rows.length, cur = rows[N - 1].count, prev = N > 1 ? rows[N - 2].count : 0;
    let msg = `<b>${n(cur)}</b> role${cur === 1 ? "" : "s"} added in the latest week`;
    if (prev) {
      const dp = Math.round(100 * (cur - prev) / prev), up = dp >= 0;
      msg += ` — <b style="color:${up ? "var(--evergreen)" : "var(--signal-deep)"}">${up ? "▲" : "▼"} ${Math.abs(dp)}%</b> vs the week before`;
    }
    return `<div class="trend-cap">${msg}.</div>`;
  }

  // day-of-week vertical bars
  function vbars(rows) {
    const hi = Math.max(1, ...rows.map(r => r.value));
    return `<div class="vbars">` + rows.map(r => `
      <div class="vbars__col">
        <div class="vbars__track"><div class="vbars__bar" style="height:${Math.max(3, r.value / hi * 100)}%"></div></div>
        <div class="vbars__lbl">${esc(r.label)}</div>
        <div class="vbars__n">${n(r.value)}</div>
      </div>`).join("") + `</div>`;
  }

  // title-keyword cloud, font-size scaled by frequency
  function cloud(rows) {
    if (!rows || !rows.length) return `<div class="empty">Not enough titles yet.</div>`;
    const hi = Math.max(...rows.map(r => r.value)), lo = Math.min(...rows.map(r => r.value));
    const size = (v) => (13 + (hi === lo ? 0 : (v - lo) / (hi - lo)) * 11).toFixed(1);
    return `<div class="kw-cloud">` + rows.map(r =>
      `<span class="kw" style="font-size:${size(r.value)}px" title="${n(r.value)} roles">${esc(r.word)}</span>`).join("") + `</div>`;
  }

  function newCosBlock(list) {
    if (!list || !list.length) return "";
    return `<div class="co-new-wrap"><div class="co-new-h">New this week</div>
      <div class="co-new">${list.map(c => `<span class="co-new__i">${esc(c)}</span>`).join("")}</div></div>`;
  }

  function freshCaption(k, unknown) {
    const bits = [];
    if (k.median_lag != null) bits.push(`median age <b>${k.median_lag} day${k.median_lag === 1 ? "" : "s"}</b>`);
    if (unknown) bits.push(`${n(unknown)} without a posting date`);
    return bits.length ? `<div class="trend-cap">${bits.join(" · ")}.</div>` : "";
  }

  // ── load ─────────────────────────────────────────────────────────────────
  async function init() {
    const city = window.PL_CITY || "berlin";
    let a, audit;
    try { [a, audit] = await Promise.all([api("/api/analytics?profile=" + city), api("/api/audit?profile=" + city)]); }
    catch { $("#kpis").innerHTML = `<div class="empty">Couldn’t load analytics.</div>`; return; }
    if (a.empty) { $("#kpis").innerHTML = `<div class="empty">No data yet for this board.</div>`; return; }

    const k = a.kpis;
    if (a.as_of) $("#asof").textContent =
      `A data report across every role we track — latest data ${fmtWeek(a.as_of)}.`;

    $("#kpis").innerHTML =
      kpi("Total roles", n(a.total)) +
      kpi("New this week", n(k.week.cur), `vs ${n(k.week.prev)} the week before`, k.week.delta, true) +
      kpi("Active companies", n(k.active_companies.cur), "hiring in the last 7 days", k.active_companies.delta) +
      kpi("New companies", n(k.new_companies), "first appeared this week") +
      kpi("Fresh ≤ 7 days", k.fresh_share == null ? "—" : k.fresh_share + "%",
        k.median_lag == null ? "share posted within a week" : `median age ${k.median_lag}d when first seen`);

    $("#trend").innerHTML = trendChart(a.intake_weekly) + trendCaption(a.intake_weekly);
    $("#workmode").innerHTML = segbar(a.work_mode, PAL);
    $("#seniority").innerHTML = rankBars(a.seniority, "value");
    $("#bySource").innerHTML = rankBars(a.by_source, "total", { chip: true, pct: true });
    $("#topCos").innerHTML = rankBars(a.top_companies, "value", { chip: true });
    $("#newCos").innerHTML = newCosBlock(a.new_companies);
    $("#freshness").innerHTML = segbar(a.freshness, FRESH_PAL) + freshCaption(k, a.fresh_unknown);
    $("#dow").innerHTML = vbars(a.dow_posting);
    $("#keywords").innerHTML = cloud(a.keywords);

    const h = audit.health || {};
    $("#health").innerHTML =
      kpi("With a company name", `${Math.round(100 * (h.with_company || 0) / Math.max(1, h.total_jobs))}%`, `${n(h.missing_company)} missing`) +
      kpi("Duplicate rows", n(h.duplicate_rows), "same posting, caught by dedup") +
      kpi("Company name variants", n(h.distinct_raw - h.distinct_clustered), "collapsed by the auditor", undefined, true) +
      kpi("Fuzzy matching", audit.fuzzy_available ? "On" : "Off", audit.fuzzy_available ? "rapidfuzz" : "exact only");
  }

  init();
})();
