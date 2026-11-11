/* ProductLab Jobs — board: browse roles, search/filter, open the listing. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const api = (p) => fetch(p).then(r => r.json());

  // filters + layout are remembered for this browser session (sessionStorage)
  const STORE_KEY = "pl_board";
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(STORE_KEY)) || {}; } catch {}

  const state = {
    q: typeof saved.q === "string" ? saved.q : "",
    sort: saved.sort || "newest",
    days: Number.isFinite(saved.days) ? saved.days : 0,
    company: Array.isArray(saved.company) ? saved.company : [],
    workType: Array.isArray(saved.workType) ? saved.workType : [],
    view: saved.view || "compact",
    page: 1, perPage: 40, pages: 1,
  };

  function persist() {
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify({
        q: state.q, sort: state.sort, days: state.days,
        company: state.company, workType: state.workType, view: state.view,
      }));
    } catch {}
  }

  const el = {
    list: $("#list"), count: $("#count"), more: $("#more"), sub: $("#sub"),
    q: $("#q"), sort: $("#sort"), days: $("#days"), view: $("#view"),
    // advanced filters
    advf: $("#advf"), advfBtn: $("#advf-btn"), advfBadge: $("#advf-badge"),
    worktype: $("#worktype"), coSearch: $("#co-search"), coList: $("#co-list"),
    advfClear: $("#advf-clear"), advfDone: $("#advf-done"),
  };

  const esc = (s) => (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function postedLabel(iso) {
    if (!iso) return "";
    const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
    if (isNaN(d)) return "";
    const days = Math.floor((Date.now() - d) / 86400000);
    if (days <= 0) return "Today";
    if (days === 1) return "Yesterday";
    if (days < 7) return days + "d ago";
    if (days < 35) return Math.round(days / 7) + "w ago";
    if (days < 365) return Math.round(days / 30) + "mo ago";
    return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
  }

  const CHECK = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M3 8.5l3.2 3.2L13 5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const EXT = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 3H3.5A1.5 1.5 0 0 0 2 4.5v8A1.5 1.5 0 0 0 3.5 14h8a1.5 1.5 0 0 0 1.5-1.5V10M9 2h5v5M14 2L7 9" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function rowHTML(j) {
    const added = postedLabel(j.scraped_date);   // show when we ADDED it, so the
                                                  // visible date matches "Recently added" order
    return `<a class="jobrow" href="${esc(j.url)}" target="_blank" rel="noopener">
      <div class="jobrow__main">
        <div class="jobrow__title">${esc(j.title_display || j.title)}</div>
        <div class="jobrow__meta">
          <span class="jobrow__co">${esc(j.company_display || j.company || "—")}</span>
          ${j.work_type ? `<span class="jobrow__sep">·</span><span>${esc(j.work_type)}</span>` : ""}
          ${added ? `<span class="jobrow__sep">·</span><span>${esc(added)}</span>` : ""}
        </div>
      </div>
      <span class="openlink" aria-hidden="true">${EXT}</span>
    </a>`;
  }

  async function load(reset) {
    if (reset) { state.page = 1; persist(); el.list.innerHTML = '<div class="skl"></div><div class="skl"></div><div class="skl"></div>'; }
    const p = new URLSearchParams({
      profile: window.PL_CITY || "berlin",
      q: state.q, sort: state.sort, days: state.days,
      page: state.page, per_page: state.perPage,
    });
    state.company.forEach(label => (coValues.get(label) || [label]).forEach(v => p.append("company", v)));
    state.workType.forEach(w => p.append("work_type", w));
    let data;
    try { data = await api("/api/jobs?" + p); }
    catch { el.count.textContent = "Couldn’t load jobs."; return; }
    state.pages = data.pages;
    const html = data.jobs.map(rowHTML).join("");
    if (reset) el.list.innerHTML = html || `<div class="empty">No jobs match. Try clearing the search or widening the date range.</div>`;
    else el.list.insertAdjacentHTML("beforeend", html);
    el.count.textContent = `${data.total.toLocaleString()} role${data.total === 1 ? "" : "s"} in ${window.PL_CITY_LABEL || "Berlin"}`;
    el.more.style.display = state.page < state.pages ? "" : "none";
  }

  // ---- events ----
  let qT;
  el.q.addEventListener("input", () => { clearTimeout(qT); qT = setTimeout(() => { state.q = el.q.value.trim(); load(true); }, 280); });
  el.sort.addEventListener("change", () => { state.sort = el.sort.value; load(true); });
  el.days.addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    el.days.querySelectorAll(".chip").forEach(c => c.classList.remove("is-on"));
    b.classList.add("is-on"); state.days = +b.dataset.d; updateBadge(); load(true);
  });

  // ---- view (density) — comfortable / compact / grid; CSS does the rest ----
  function applyView() {
    el.list.classList.remove("joblist--comfortable", "joblist--compact", "joblist--grid");
    el.list.classList.add("joblist--" + state.view);
    el.view.querySelectorAll(".chip").forEach(c => c.classList.toggle("is-on", c.dataset.v === state.view));
  }
  el.view.addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    state.view = b.dataset.v;
    persist();
    applyView();
  });

  // ---- advanced filters ----
  function updateBadge() {
    const n = (state.days > 0 ? 1 : 0) + state.workType.length + state.company.length;
    el.advf.classList.toggle("has-active", n > 0);
    if (n > 0) { el.advfBadge.textContent = n; el.advfBadge.hidden = false; }
    else el.advfBadge.hidden = true;
  }

  // companies are grouped by cleaned label; each row filters on its raw names.
  let companies = null;         // [{label,count,values:[raw,...]}] — lazy-loaded
  const coValues = new Map();   // label -> [raw names] (for request expansion)
  async function ensureCompanies() {
    if (companies !== null) return;
    el.coList.innerHTML = '<div class="advf__empty">Loading…</div>';
    try {
      const d = await api("/api/companies?profile=" + encodeURIComponent(window.PL_CITY || "berlin"));
      companies = d.companies || [];
    } catch { companies = []; }
    companies.forEach(c => coValues.set(c.label, c.values || []));
    renderCompanies();
  }
  function renderCompanies() {
    if (companies === null) { el.coList.innerHTML = '<div class="advf__empty">Loading…</div>'; return; }
    const term = (el.coSearch.value || "").trim().toLowerCase();
    const sel = new Set(state.company);
    const match = (c) => !term || (c.label || "").toLowerCase().includes(term)
      || (c.values || []).some(v => (v || "").toLowerCase().includes(term));
    const picked = companies.filter(c => sel.has(c.label));          // always shown
    const rest = companies.filter(c => !sel.has(c.label) && match(c));
    const shown = [...picked, ...rest].slice(0, 240);
    if (!shown.length) { el.coList.innerHTML = '<div class="advf__empty">No companies match.</div>'; return; }
    el.coList.innerHTML = shown.map(c => `
      <button type="button" class="advf__opt${sel.has(c.label) ? " is-on" : ""}" data-co="${esc(c.label)}">
        <span class="advf__box">${CHECK}</span>
        <span class="advf__co">${esc(c.label)}</span>
        <span class="advf__cnt">${c.count}</span>
      </button>`).join("");
  }

  el.worktype.addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    const w = b.dataset.w, i = state.workType.indexOf(w);
    if (i >= 0) state.workType.splice(i, 1); else state.workType.push(w);
    b.classList.toggle("is-on"); updateBadge(); load(true);
  });
  el.coList.addEventListener("click", (e) => {
    const b = e.target.closest(".advf__opt"); if (!b) return;
    const label = b.dataset.co, i = state.company.indexOf(label);
    if (i >= 0) state.company.splice(i, 1); else state.company.push(label);
    renderCompanies(); updateBadge(); load(true);
  });
  el.coSearch.addEventListener("input", renderCompanies);

  el.advfBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = el.advf.classList.toggle("is-open");
    el.advfBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) ensureCompanies();
  });
  $("#advf-panel").addEventListener("click", (e) => e.stopPropagation());
  function closeAdvf() { el.advf.classList.remove("is-open"); el.advfBtn.setAttribute("aria-expanded", "false"); }
  el.advfDone.addEventListener("click", closeAdvf);
  document.addEventListener("click", closeAdvf);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAdvf(); });
  el.advfClear.addEventListener("click", () => {
    state.days = 0; state.workType = []; state.company = [];
    el.days.querySelectorAll(".chip").forEach(c => c.classList.toggle("is-on", c.dataset.d === "0"));
    el.worktype.querySelectorAll(".chip").forEach(c => c.classList.remove("is-on"));
    el.coSearch.value = "";
    renderCompanies(); updateBadge(); load(true);
  });

  el.more.addEventListener("click", () => { state.page++; load(false); });

  async function init() {
    // reflect any session-saved filters back into the controls
    el.q.value = state.q;
    el.sort.value = state.sort;
    el.days.querySelectorAll(".chip").forEach(c => c.classList.toggle("is-on", +c.dataset.d === state.days));
    el.worktype.querySelectorAll(".chip").forEach(c => c.classList.toggle("is-on", state.workType.includes(c.dataset.w)));
    // load company labels up front so saved picks expand to the right raw names
    if (state.company.length) await ensureCompanies();
    applyView();
    updateBadge();
    load(true);
  }
  init();
})();
