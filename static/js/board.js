/* ProductLab Jobs — board: browse Berlin jobs, select 3–10, hand to composer. */
(() => {
  "use strict";
  const MAX = 10, MIN = 3;
  const $ = (s, r = document) => r.querySelector(s);
  const api = (p) => fetch(p).then(r => r.json());

  const state = {
    q: "", sort: "posted", days: 0, page: 1, perPage: 40, pages: 1,
    selected: new Map(),   // id -> {id,title,company,work_type,url}
    jobsById: {},          // id -> job (for selection, across pages)
  };

  const el = {
    list: $("#list"), count: $("#count"), more: $("#more"), sub: $("#sub"),
    q: $("#q"), sort: $("#sort"), days: $("#days"),
    selbar: $("#selbar"), seln: $("#seln"), selhint: $("#selhint"),
    makebtn: $("#makebtn"), selclear: $("#selclear"),
    scrapeBtn: $("#scrape-btn"), scrapeLabel: $("#scrape-label"), toast: $("#toast"),
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
  let toastT;
  function toast(msg, err) {
    el.toast.textContent = msg; el.toast.classList.toggle("is-err", !!err);
    el.toast.classList.add("is-show");
    clearTimeout(toastT); toastT = setTimeout(() => el.toast.classList.remove("is-show"), 3200);
  }

  const CHECK = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M3 8.5l3.2 3.2L13 5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const EXT = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 3H3.5A1.5 1.5 0 0 0 2 4.5v8A1.5 1.5 0 0 0 3.5 14h8a1.5 1.5 0 0 0 1.5-1.5V10M9 2h5v5M14 2L7 9" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function rowHTML(j) {
    const sel = state.selected.has(j.id);
    const atMax = state.selected.size >= MAX && !sel;
    const posted = postedLabel(j.posted_date);
    return `<div class="jobrow ${sel ? "is-sel" : ""}" data-id="${j.id}">
      <div class="jobrow__check ${atMax ? "is-disabled" : ""}" role="checkbox" aria-checked="${sel}">${CHECK}</div>
      <div class="jobrow__main">
        <div class="jobrow__title">${esc(j.title_display || j.title)}</div>
        <div class="jobrow__meta">
          <span class="jobrow__co">${esc(j.company_display || j.company || "—")}</span>
          ${j.work_type ? `<span class="jobrow__sep">·</span><span>${esc(j.work_type)}</span>` : ""}
          ${posted ? `<span class="jobrow__sep">·</span><span>${esc(posted)}</span>` : ""}
        </div>
      </div>
      <div class="jobrow__right">
        <a class="openlink" href="${esc(j.url)}" target="_blank" rel="noopener" title="Open listing" data-noselect>${EXT}</a>
      </div>
    </div>`;
  }

  async function load(reset) {
    if (reset) { state.page = 1; el.list.innerHTML = '<div class="skl"></div><div class="skl"></div><div class="skl"></div>'; }
    const p = new URLSearchParams({
      city: "Berlin", status: "all", lang: "all",
      q: state.q, sort: state.sort, days: state.days,
      page: state.page, per_page: state.perPage,
    });
    let data;
    try { data = await api("/api/jobs?" + p); }
    catch { el.count.textContent = "Couldn’t load jobs."; return; }
    state.pages = data.pages;
    if (reset) state.jobsById = {};
    data.jobs.forEach(j => {
      state.jobsById[j.id] = {
        id: j.id,
        title: j.title_display || j.title,
        company: j.company_display || j.company || "",
        work_type: j.work_type || "",
        url: j.url,
      };
    });
    const html = data.jobs.map(rowHTML).join("");
    if (reset) el.list.innerHTML = html || `<div class="empty">No jobs match. Try clearing the search or widening the date range.</div>`;
    else el.list.insertAdjacentHTML("beforeend", html);
    el.count.textContent = `${data.total.toLocaleString()} role${data.total === 1 ? "" : "s"} in Berlin`;
    el.more.style.display = state.page < state.pages ? "" : "none";
    syncRows();
  }

  // keep checkbox visuals in sync with the selection set (e.g. after re-render)
  function syncRows() {
    const atMax = state.selected.size >= MAX;
    el.list.querySelectorAll(".jobrow").forEach(row => {
      const id = +row.dataset.id, sel = state.selected.has(id);
      row.classList.toggle("is-sel", sel);
      const chk = row.querySelector(".jobrow__check");
      chk.setAttribute("aria-checked", sel);
      chk.classList.toggle("is-disabled", atMax && !sel);
    });
  }

  function toggle(row) {
    const id = +row.dataset.id;
    if (state.selected.has(id)) state.selected.delete(id);
    else {
      if (state.selected.size >= MAX) { toast(`You can select up to ${MAX} jobs.`); return; }
      const job = state.jobsById[id];
      if (job) state.selected.set(id, job);
    }
    syncRows(); syncSelbar();
  }

  function syncSelbar() {
    const n = state.selected.size;
    el.seln.textContent = n;
    el.selbar.classList.toggle("is-show", n > 0);
    el.makebtn.disabled = n < MIN;
    el.selhint.textContent = n < MIN ? `Select at least ${MIN}` : n >= MAX ? "Maximum reached" : `Up to ${MAX}`;
    el.makebtn.textContent = n >= MIN ? `Create LinkedIn post (${n}) →` : "Create LinkedIn post →";
  }

  // ---- events ----
  el.list.addEventListener("click", (e) => {
    if (e.target.closest("[data-noselect]")) return;
    const row = e.target.closest(".jobrow"); if (row) toggle(row);
  });
  let qT;
  el.q.addEventListener("input", () => { clearTimeout(qT); qT = setTimeout(() => { state.q = el.q.value.trim(); load(true); }, 280); });
  el.sort.addEventListener("change", () => { state.sort = el.sort.value; load(true); });
  el.days.addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    el.days.querySelectorAll(".chip").forEach(c => c.classList.remove("is-on"));
    b.classList.add("is-on"); state.days = +b.dataset.d; load(true);
  });
  el.more.addEventListener("click", () => { state.page++; load(false); });
  el.selclear.addEventListener("click", (e) => { e.preventDefault(); state.selected.clear(); syncRows(); syncSelbar(); });
  el.makebtn.addEventListener("click", () => {
    if (state.selected.size < MIN) return;
    sessionStorage.setItem("pl_selected", JSON.stringify([...state.selected.values()]));
    location.href = "/compose";
  });

  // ---- fetch (admin-gated) ----
  // The board auto-refreshes daily at 5 AM, so manual fetching is disabled for
  // normal visitors: a single click does nothing. Five quick clicks reveal an
  // admin password prompt; the password is validated server-side (never shipped
  // to the browser), so this is a convenience gesture, not the real gate.
  let pollT, clickN = 0, clickTimer = null;
  el.scrapeBtn.title = "Jobs auto-update daily at 5 AM";
  el.scrapeBtn.addEventListener("click", () => {
    if (el.scrapeBtn.classList.contains("is-busy")) return;
    clickN++;
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => { clickN = 0; }, 1500);
    if (clickN < 5) return;               // normal visitors: no-op
    clickN = 0;
    const pw = prompt("Admin password to fetch new jobs:");
    if (pw) startFetch(pw);
  });
  async function startFetch(pw) {
    el.scrapeBtn.classList.add("is-busy"); el.scrapeLabel.textContent = "Fetching…";
    try {
      const r = await fetch("/api/scrape", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw }),
      });
      if (r.status === 403) { toast("Wrong password — fetching stays disabled.", true); resetScrape(); return; }
      if (r.status === 409) { toast("A fetch is already running."); resetScrape(); return; }
      toast("Fetching new Berlin jobs — this takes a few minutes.");
      pollT = setInterval(pollScrape, 3000);
    } catch { toast("Couldn’t start fetch.", true); resetScrape(); }
  }
  async function pollScrape() {
    let s; try { s = await api("/api/scrape/status"); } catch { return; }
    if (!s.running && s.result) {
      clearInterval(pollT); resetScrape();
      if (s.result.ok) { toast(`Done — ${s.result.added || 0} new job(s) added.`); load(true); }
      else toast("Fetch failed: " + (s.result.error || "unknown"), true);
    }
  }
  function resetScrape() { el.scrapeBtn.classList.remove("is-busy"); el.scrapeLabel.textContent = "Fetch new"; }

  // resume a scrape that was already running when the page loaded
  api("/api/scrape/status").then(s => { if (s && s.running) { el.scrapeBtn.classList.add("is-busy"); el.scrapeLabel.textContent = "Fetching…"; pollT = setInterval(pollScrape, 3000); } }).catch(() => {});

  syncSelbar();
  load(true);
})();
