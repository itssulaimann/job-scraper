/* Shared city selector — renders a custom dropdown into the page heading
   (#city-anchor) and sets window.PL_CITY / window.PL_CITY_LABEL for the page
   scripts. Loaded before board.js / analytics.js. */
(() => {
  "use strict";
  const KEY = "pl_city";
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  window.PL_CITY = localStorage.getItem(KEY) || "berlin";
  window.PL_CITY_LABEL = cap(window.PL_CITY);

  const anchor = document.getElementById("city-anchor");
  if (anchor) anchor.textContent = window.PL_CITY_LABEL;   // sync placeholder

  const CARET = '<svg class="city-dd__caret" viewBox="0 0 12 8" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 1.5l5 5 5-5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function build(profiles, def) {
    if (!anchor) return;
    const keys = Object.keys(profiles);
    if (!keys.length) return;
    if (!profiles[window.PL_CITY]) window.PL_CITY = def || keys[0];
    window.PL_CITY_LABEL = (profiles[window.PL_CITY] || {}).label || window.PL_CITY_LABEL;

    anchor.innerHTML =
      `<span class="city-dd" id="city-dd">
        <button type="button" class="city-dd__btn" aria-haspopup="listbox" aria-expanded="false">
          <span class="city-dd__label">${window.PL_CITY_LABEL}</span>${CARET}
        </button>
        <div class="city-dd__menu" role="listbox">
          ${keys.map(k => `<button type="button" class="city-dd__opt${k === window.PL_CITY ? " is-on" : ""}" role="option" data-city="${k}">${profiles[k].label || k}</button>`).join("")}
        </div>
      </span>`;

    const dd = anchor.querySelector(".city-dd");
    const btn = dd.querySelector(".city-dd__btn");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = dd.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    dd.querySelector(".city-dd__menu").addEventListener("click", (e) => {
      const o = e.target.closest(".city-dd__opt");
      if (!o) return;
      localStorage.setItem(KEY, o.dataset.city);
      location.reload();
    });
    document.addEventListener("click", () => {
      dd.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") dd.classList.remove("is-open"); });
  }

  fetch("/api/profiles").then(r => r.json())
    .then(d => build(d.profiles || {}, d.default))
    .catch(() => build({ [window.PL_CITY]: { label: window.PL_CITY_LABEL } }, window.PL_CITY));
})();
