/* ProductLab Jobs — composer: selected jobs → branded LinkedIn cards + export. */
(() => {
  "use strict";
  const FULL = 1080, PREVIEW = 360, SCALE = PREVIEW / FULL;
  const $ = (s) => document.querySelector(s);
  const H2I = window.htmlToImage;

  const jobs = (() => {
    try { return JSON.parse(sessionStorage.getItem("pl_selected") || "[]"); }
    catch { return []; }
  })();
  if (!jobs.length) { location.href = "/"; return; }

  const esc = (s) => (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  const now = new Date();
  const defaultDate = `${MONTHS[now.getMonth()]} ${now.getFullYear()}`;

  const el = {
    thumbs: $("#thumbs"), toast: $("#toast"),
    date: $("#f-date"), cover1: $("#f-cover1"), cover2: $("#f-cover2"), sub: $("#f-sub"),
    sum1: $("#f-sum1"), sum2: $("#f-sum2"),
    tCover: $("#t-cover"), tSummary: $("#t-summary"),
    dlPdf: $("#dl-pdf"), dlZip: $("#dl-zip"), dlSum: $("#dl-sum"),
  };
  el.date.value = defaultDate;
  el.sub.value = `${jobs.length} new roles in Berlin — hand-picked this week.`;

  let toastT;
  const toast = (m, err) => { el.toast.textContent = m; el.toast.classList.toggle("is-err", !!err); el.toast.classList.add("is-show"); clearTimeout(toastT); toastT = setTimeout(() => el.toast.classList.remove("is-show"), 3400); };

  let LOGO = "";
  let carousel = [];   // .plc nodes in carousel order (cover + jobs)
  let summaryNode = null;

  // ---- card builders (return a full-size .plc node) ----
  function card(cls) {
    const d = document.createElement("div");
    d.className = "plc " + cls;
    return d;
  }
  function logoTop(dateText) {
    return `<div class="plc__top"><span class="plc__logo">${LOGO}</span><span class="plc__date">${esc(dateText)}</span></div>`;
  }
  const footIdx = (i, n) => `<div class="plc__foot">${String(i).padStart(2, "0")} / ${String(n).padStart(2, "0")}</div>`;

  function coverCard(total) {
    const c = card("plc--cover");
    c.innerHTML = logoTop(el.date.value) +
      `<div class="plc__mid">
         <div class="plc__headline"><span class="mint">${esc(el.cover1.value)}</span><br>${esc(el.cover2.value)}</div>
         <hr class="plc__rule">
         <div class="plc__sub">${esc(el.sub.value)}</div>
       </div>` + footIdx(1, total);
    return c;
  }
  function jobCard(job, idx, total) {
    const c = card("plc--job");
    const work = job.work_type ? `<div class="plc__work">${esc(job.work_type)}</div>` : "";
    c.innerHTML = logoTop(el.date.value) +
      `<div class="plc__mid">
         <div class="plc__headline">${esc(job.title)}</div>
         <div class="plc__company">${esc(job.company)}</div>
         ${work}
       </div>` + footIdx(idx, total);
    return c;
  }
  function summaryCard() {
    const c = card("plc--sum");
    const n = jobs.length;
    // scale row density to fit up to 10 rows
    const tSize = n <= 5 ? 31 : n <= 7 ? 27 : n <= 8 ? 24 : 21;
    const pad = n <= 5 ? 22 : n <= 7 ? 17 : n <= 8 ? 14 : 11;
    const rows = jobs.map((j, i) => `
      <div class="plc-row" style="padding:${pad}px 0">
        <span class="plc-row__n" style="font-size:${Math.round(tSize * .72)}px">${String(i + 1).padStart(2, "0")}</span>
        <span class="plc-row__t" style="font-size:${tSize}px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(j.title)}</span>
        <span class="plc-row__c" style="font-size:${Math.round(tSize * .8)}px;max-width:360px;overflow:hidden;text-overflow:ellipsis">${esc(j.company)}</span>
      </div>`).join("");
    c.innerHTML = logoTop(el.date.value) +
      `<div class="plc__mid">
         <div class="plc__headline">${esc(el.sum1.value)}<br><span class="ever">${esc(el.sum2.value)}</span></div>
         <div class="plc-list">${rows}</div>
       </div>`;
    return c;
  }

  // shrink a headline until it fits a max height
  function fitHeadline(node, maxH, start, min) {
    let s = start; node.style.fontSize = s + "px";
    while (node.scrollHeight > maxH && s > min) { s -= 3; node.style.fontSize = s + "px"; }
  }

  function thumb(node, cap) {
    const wrap = document.createElement("div");
    wrap.innerHTML = `<div class="thumbwrap" style="width:${PREVIEW}px;height:${PREVIEW}px">
        <div style="transform:scale(${SCALE});transform-origin:top left;width:${FULL}px;height:${FULL}px"></div>
      </div><div class="thumbcap">${esc(cap)}</div>`;
    wrap.querySelector(".thumbwrap > div").appendChild(node);
    return wrap;
  }

  function build() {
    el.thumbs.innerHTML = "";
    carousel = []; summaryNode = null;
    const total = (el.tCover.checked ? 1 : 0) + jobs.length;
    let idx = 0;
    const frag = document.createDocumentFragment();

    if (el.tCover.checked) {
      idx++; const c = coverCard(total);
      carousel.push(c); frag.appendChild(thumb(c, `Cover · ${idx}/${total}`));
    }
    jobs.forEach((j) => {
      idx++; const c = jobCard(j, idx, total);
      carousel.push(c);
      frag.appendChild(thumb(c, `${idx}/${total} · ${j.company}`));
    });
    if (el.tSummary.checked) {
      summaryNode = summaryCard();
      frag.appendChild(thumb(summaryNode, "Summary card (single image)"));
    }
    el.thumbs.appendChild(frag);
    // fit job-card headlines after they are in the DOM
    carousel.forEach(c => { if (c.classList.contains("plc--job")) fitHeadline(c.querySelector(".plc__headline"), 360, 82, 40); });
  }

  // ---- export ----
  let fontCSS = null;
  async function toPng(node) {
    if (fontCSS === null) { try { fontCSS = await H2I.getFontEmbedCSS(node); } catch { fontCSS = ""; } }
    return H2I.toPng(node, { pixelRatio: 2, cacheBust: true, width: FULL, height: FULL, fontEmbedCSS: fontCSS });
  }
  const dl = (href, name) => { const a = document.createElement("a"); a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove(); };

  async function withBusy(btn, fn) {
    btn.classList.add("is-busy"); btn.disabled = true;
    try { await fn(); } catch (e) { console.error(e); toast("Export failed — try again.", true); }
    finally { btn.classList.remove("is-busy"); btn.disabled = false; }
  }

  el.dlPdf.addEventListener("click", () => withBusy(el.dlPdf, async () => {
    if (!carousel.length) return;
    const { jsPDF } = window.jspdf;
    const pdf = new jsPDF({ unit: "px", format: [FULL, FULL], orientation: "portrait" });
    for (let i = 0; i < carousel.length; i++) {
      const png = await toPng(carousel[i]);
      if (i > 0) pdf.addPage([FULL, FULL], "portrait");
      pdf.addImage(png, "PNG", 0, 0, FULL, FULL);
    }
    pdf.save("productlab-jobs-carousel.pdf");
    toast("Carousel PDF downloaded — upload it to LinkedIn as a document.");
  }));

  el.dlZip.addEventListener("click", () => withBusy(el.dlZip, async () => {
    const zip = new JSZip();
    for (let i = 0; i < carousel.length; i++) {
      const png = await toPng(carousel[i]);
      zip.file(`card-${String(i + 1).padStart(2, "0")}.png`, png.split(",")[1], { base64: true });
    }
    if (summaryNode) zip.file("summary.png", (await toPng(summaryNode)).split(",")[1], { base64: true });
    const blob = await zip.generateAsync({ type: "blob" });
    dl(URL.createObjectURL(blob), "productlab-jobs-cards.zip");
    toast("All cards downloaded as PNGs.");
  }));

  el.dlSum.addEventListener("click", () => withBusy(el.dlSum, async () => {
    if (!summaryNode) { toast("Enable the summary card first."); return; }
    dl(await toPng(summaryNode), "productlab-jobs-summary.png");
  }));

  // rebuild on edits
  let rb;
  const onEdit = () => { clearTimeout(rb); rb = setTimeout(build, 180); };
  [el.date, el.cover1, el.cover2, el.sub, el.sum1, el.sum2].forEach(i => i.addEventListener("input", onEdit));
  [el.tCover, el.tSummary].forEach(i => i.addEventListener("change", build));

  // boot
  fetch("/static/brand/productlab-logo.svg").then(r => r.text()).then(svg => { LOGO = svg; build(); })
    .catch(() => { LOGO = "Productlab"; build(); });
})();
