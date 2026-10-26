/* Admin-gated "fetch new jobs" control. Lives on the Analytics page as a near-
   invisible button: a single click does nothing; five quick clicks reveal an
   admin password prompt, validated server-side. Scrapes the selected city. */
(() => {
  "use strict";
  const btn = document.getElementById("scrape-btn");
  if (!btn) return;
  const toastEl = document.getElementById("toast");
  let toastT;
  const toast = (m, err) => {
    if (!toastEl) return;
    toastEl.textContent = m;
    toastEl.classList.toggle("is-err", !!err);
    toastEl.classList.add("is-show");
    clearTimeout(toastT);
    toastT = setTimeout(() => toastEl.classList.remove("is-show"), 3400);
  };

  let pollT, clickN = 0, clickTimer = null;

  btn.addEventListener("click", () => {
    if (btn.classList.contains("is-busy")) return;
    clickN++;
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => { clickN = 0; }, 1500);
    if (clickN < 5) return;          // normal visitors: no-op
    clickN = 0;
    const pw = prompt("Admin password to fetch new jobs:");
    if (pw) start(pw);
  });

  async function start(pw) {
    btn.classList.add("is-busy");
    try {
      const r = await fetch("/api/scrape", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw, profile: window.PL_CITY || "berlin" }),
      });
      if (r.status === 403) { toast("Wrong password — fetching stays disabled.", true); btn.classList.remove("is-busy"); return; }
      if (r.status === 409) { toast("A fetch is already running."); btn.classList.remove("is-busy"); return; }
      toast("Fetching new jobs — this takes a few minutes.");
      pollT = setInterval(poll, 3000);
    } catch { toast("Couldn’t start fetch.", true); btn.classList.remove("is-busy"); }
  }

  async function poll() {
    let s; try { s = await fetch("/api/scrape/status").then(r => r.json()); } catch { return; }
    if (!s.running && s.result) {
      clearInterval(pollT);
      btn.classList.remove("is-busy");
      if (s.result.ok) toast(`Done — ${s.result.added || 0} new job(s) added.`);
      else toast("Fetch failed: " + (s.result.error || "unknown"), true);
    }
  }

  fetch("/api/scrape/status").then(r => r.json())
    .then(s => { if (s && s.running) { btn.classList.add("is-busy"); pollT = setInterval(poll, 3000); } })
    .catch(() => {});
})();
