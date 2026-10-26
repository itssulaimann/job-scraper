/* ProductLab Jobs — analytics: market overview + data-health / company auditor. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const api = (p) => fetch(p).then(r => r.json());
  const esc = (s) => (s || "").toString().replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const n = (x) => (x || 0).toLocaleString();

  const stat = (k, v, sub, accent) =>
    `<div class="statcard"><div class="statcard__k">${esc(k)}</div>
       <div class="statcard__v ${accent ? "accent" : ""}">${v}</div>
       ${sub ? `<div class="statcard__sub">${esc(sub)}</div>` : ""}</div>`;

  function bars(rows, { label = "label", value = "value", max } = {}) {
    const hi = max || Math.max(1, ...rows.map(r => r[value]));
    return `<div style="display:flex;flex-direction:column;gap:9px;margin-top:6px">` +
      rows.map(r => `
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:170px;flex:none;font-size:13px;color:var(--slate);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r[label] || "—")}</div>
          <div style="flex:1;background:var(--mist);border-radius:999px;height:16px;overflow:hidden">
            <div style="height:100%;width:${Math.max(2, r[value] / hi * 100)}%;background:var(--jade);border-radius:999px"></div>
          </div>
          <div style="width:46px;flex:none;text-align:right;font-size:13px;font-weight:500;color:var(--evergreen)">${n(r[value])}</div>
        </div>`).join("") + `</div>`;
  }

  async function init() {
    const city = window.PL_CITY || "berlin";
    let stats, audit;
    try { [stats, audit] = await Promise.all([api("/api/stats?profile=" + city), api("/api/audit?profile=" + city)]); }
    catch { $("#kpis").innerHTML = `<div class="empty">Couldn’t load analytics.</div>`; return; }

    const h = audit.health || {};
    $("#kpis").innerHTML =
      stat("Total roles", n(stats.total)) +
      stat("New this week", n(stats.week), "scraped in the last 7 days", true) +
      stat("Companies", n(h.distinct_clustered || stats.companies), `${n(h.distinct_raw)} raw names, deduped`) +
      stat("Sources", n((stats.by_source || []).length), "free scrapers");

    $("#bySource").innerHTML = bars(stats.by_source || []);
    $("#topCos").innerHTML = bars((stats.top_companies || []).slice(0, 12));

    $("#health").innerHTML =
      stat("With a company name", `${Math.round(100 * (h.with_company || 0) / Math.max(1, h.total_jobs))}%`, `${n(h.missing_company)} missing`) +
      stat("Duplicate rows", n(h.duplicate_rows), "same posting, caught by dedup") +
      stat("Company name variants", n(h.distinct_raw - h.distinct_clustered), "collapsed by the auditor", true) +
      stat("Fuzzy matching", audit.fuzzy_available ? "On" : "Off", audit.fuzzy_available ? "rapidfuzz" : "exact only");

  }

  init();
})();
