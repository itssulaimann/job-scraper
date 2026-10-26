/* ════════════════════════════════════════════════════════════════════════
   JobFinder — board logic
   Triage flow: Inbox → Saved → Applied (+ Hidden) · faceted filters ·
   keyboard-first · optimistic status changes with undo
   ════════════════════════════════════════════════════════════════════════ */

// ── State ──────────────────────────────────────────────────────────────
const state = {
  profile:   '',           // active profile key (cookie-backed)
  profiles:  {},           // { key: { label, short, templates } }
  status:    'inbox',
  q:         '',
  source:    new Set(),
  seniority: new Set(),
  city:      new Set(),
  fit:       new Set(),
  lang:      new Set(),   // German gate; empty = gate on (German hidden)
  days:      0,
  sort:      'newest',
  page:      1,
  perPage:   40,
  pages:     1,
  total:     0,
  selected:  null,
  jobs:      [],          // current page of jobs
  sourcesExpanded: false,
};

let _loadCtl     = null;   // abort in-flight list fetch
let _selectToken = 0;      // ignore stale detail fetches
let _lastAction  = null;   // { id, prevStatus } for undo
let _toastTimer  = null;
let _scrapePoll  = null;   // interval id for scrape status polling

const $ = id => document.getElementById(id);

// ── Profile cookie ────────────────────────────────────────────────────
function getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}
function setCookie(name, val) {
  document.cookie = `${name}=${encodeURIComponent(val)};path=/;max-age=${365*86400}`;
}

// ── Helpers ────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Quick-pick reasons for hiding a job. Served by /api/preferences so the
// scorer's vocabulary and the UI's cannot drift apart; this is only the
// fallback for when that fetch fails.
let _hideReasons = [
  'too senior / exec', 'too junior', 'wrong role type',
  'location / on-site', 'language requirement', 'company / industry',
];

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const days = Math.floor((Date.now() - new Date(dateStr)) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return '1d';
  if (days < 7)  return `${days}d`;
  if (days < 30) return `${Math.floor(days / 7)}w`;
  return new Date(dateStr).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

function dateGroupLabel(dateStr) {
  if (!dateStr) return 'Older';
  const diff = Math.floor((Date.now() - new Date(dateStr)) / 86400000);
  if (diff <= 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  if (diff <= 7)  return 'This week';
  if (diff <= 30) return 'This month';
  return 'Older';
}

// Descriptions arrive as raw text with occasional HTML/markup debris —
// strip tags and collapse whitespace before display.
function cleanDescription(raw) {
  if (!raw) return '';
  const doc  = new DOMParser().parseFromString(raw, 'text/html');
  let text   = doc.body.textContent || '';
  text = text.replace(/<[a-z-]+>|<\/[a-z-]+>/gi, ' ');       // pseudo-tags like <gh-intro>
  text = text.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  return text;
}

const FIT_CLASSES = { 'Strong fit': 'strong', 'Weak fit': 'weak', 'Neutral': 'neutral' };

function fitClass(bucket) { return FIT_CLASSES[bucket] || 'neutral'; }

// LLM rubric verdict (Strong/Good/Moderate/Weak/Poor Fit) -> same 3-tone palette
// as the fit bucket badge.
const LLM_CLASSES = {
  'Strong Fit': 'strong', 'Good Fit': 'strong',
  'Moderate Fit': 'neutral',
  'Weak Fit': 'weak', 'Poor Fit': 'weak',
};
function llmClass(verdict) { return LLM_CLASSES[verdict] || 'neutral'; }

// Six-dimension LLM rubric (llm_fit_scorer.py) — the sole fit signal.
// 'verdict' dims (PASS/FAIL/FLAG) render their verdict instead of a score.
const LLM_DIMS = [
  ['technical_skills',  'Technical skills'],
  ['experience_match',  'Experience match'],
  ['culture_fit',       'Culture fit'],
  ['location',          'Location',  'verdict'],
  ['language',          'Language',  'verdict'],
  ['career_alignment',  'Career alignment'],
];

function llmSection(job) {
  const dims = job.llm_fit_dims;
  if (!dims) return '';

  const rows = LLM_DIMS.map(([key, label, kind]) => {
    const d = dims[key] || {};
    const isVerdict = kind === 'verdict';
    const value = isVerdict ? escHtml(d.verdict || '—') : (d.score ?? '—');
    const cls   = isVerdict ? llmClass(d.verdict === 'PASS' ? 'Strong Fit' : d.verdict === 'FAIL' ? 'Poor Fit' : 'Moderate Fit')
                             : llmClass(d.score >= 65 ? 'Strong Fit' : d.score >= 35 ? 'Moderate Fit' : 'Poor Fit');
    return `<div class="llmdim">
      <div class="llmdim__row"><span class="llmdim__k">${label}</span><span class="llmdim__v fact__v--${cls}">${value}</span></div>
      ${d.note ? `<p class="llmdim__note">${escHtml(d.note)}</p>` : ''}
    </div>`;
  }).join('');

  const pills = [
    ...(dims.strengths || []).map(s => `<span class="sig sig--pos">+ ${escHtml(s)}</span>`),
    ...(dims.gaps || []).map(g => `<span class="sig sig--neg">− ${escHtml(g)}</span>`),
  ].join('');

  return `
    <div class="dt__section-title">Why this fit score</div>
    <div class="dt__llm">${rows}</div>
    ${pills ? `<div class="dt__sigs">${pills}</div>` : ''}
  `;
}

function lvlClass(seniority) {
  const s = (seniority || '').toLowerCase();
  if (s.includes('junior'))   return 'row__lvl--junior';
  if (s.includes('director')) return 'row__lvl--director';
  return 'row__lvl--senior';
}

function isMobileDetail() { return window.matchMedia('(max-width: 1080px)').matches; }

// ── Query params ───────────────────────────────────────────────────────
function filterParams() {
  return new URLSearchParams({
    q:         state.q,
    source:    [...state.source].join(','),
    seniority: [...state.seniority].join(','),
    city:      [...state.city].join(','),
    fit:       [...state.fit].join(','),
    lang:      [...state.lang].join(','),
    days:      state.days,
    status:    state.status,
    profile:   state.profile,
  });
}

function hasActiveFilters() {
  return !!(state.q || state.source.size || state.seniority.size ||
            state.city.size || state.fit.size || state.lang.size || state.days > 0);
}

// ── Load: list + facets ────────────────────────────────────────────────
async function refresh({ keepSelection = false } = {}) {
  if (!keepSelection) state.selected = null;
  loadJobs();
  loadFacets();
}

async function loadJobs() {
  if (_loadCtl) _loadCtl.abort();
  _loadCtl = new AbortController();

  const skel  = $('list-skeleton');
  const list  = $('job-list');
  const empty = $('empty-state');

  if (!list.children.length) skel.hidden = false;
  empty.hidden = true;

  const params = filterParams();
  params.set('sort', state.sort);
  params.set('page', state.page);
  params.set('per_page', state.perPage);

  let data;
  try {
    data = await (await fetch(`/api/jobs?${params}`, { signal: _loadCtl.signal })).json();
  } catch (e) {
    if (e.name === 'AbortError') return;
    skel.hidden = true;
    $('results-count').textContent = 'Failed to load — is the server running?';
    return;
  }

  skel.hidden = true;
  state.jobs  = data.jobs;
  state.total = data.total;
  state.pages = data.pages;

  $('results-count').textContent =
    `${data.total.toLocaleString()} listing${data.total === 1 ? '' : 's'}`;
  $('page-indicator').textContent = data.pages > 1 ? `p. ${data.page}` : '';

  renderList();
  renderPagination();

  // keep or establish a selection (desktop only auto-select)
  if (state.jobs.length === 0) {
    showEmptyState();
    clearDetail();
    return;
  }
  const stillThere = state.jobs.some(j => j.id === state.selected);
  if (!stillThere) {
    if (!isMobileDetail()) selectJob(state.jobs[0].id, { scroll: false });
    else clearDetail();
  } else {
    highlightSelected();
  }
}

async function loadFacets() {
  let meta;
  try {
    meta = await (await fetch(`/api/meta?${filterParams()}`)).json();
  } catch { return; }

  // status tab counts
  for (const [key, n] of Object.entries(meta.statuses)) {
    const el = document.querySelector(`[data-count="${key}"]`);
    if (el) el.textContent = n.toLocaleString();
  }

  renderFacet('facet-fit',       meta.fits,        state.fit);
  renderFacet('facet-lang',      meta.langs || [],  state.lang);
  renderFacet('facet-city',      meta.cities,      state.city);
  renderFacet('facet-seniority', meta.seniorities, state.seniority);
  renderFacet('facet-source',    meta.sources,     state.source, { collapsible: true });

  $('clear-filters').hidden = !hasActiveFilters();
  $('filters-dot').hidden   = !hasActiveFilters();
}

function renderFacet(containerId, rows, selectedSet, { collapsible = false } = {}) {
  const el  = $(containerId);
  const dim = containerId.replace('facet-', '');

  // Selected values that fell to zero under other filters must stay visible
  const known = new Set(rows.map(r => r.label));
  const extra = [...selectedSet].filter(v => !known.has(v))
    .map(v => ({ label: v, count: 0 }));
  let items = [...rows, ...extra];

  const LIMIT = 8;
  let hidden = 0;
  if (collapsible && !state.sourcesExpanded && items.length > LIMIT) {
    const kept = items.slice(0, LIMIT);
    // never collapse away an actively selected source
    for (const it of items.slice(LIMIT)) if (selectedSet.has(it.label)) kept.push(it);
    hidden = items.length - kept.length;
    items = kept;
  }

  el.innerHTML = items.map(({ label, count }) => `
    <button class="facet${selectedSet.has(label) ? ' is-on' : ''}${count === 0 ? ' is-zero' : ''}"
            data-dim="${dim}" data-value="${escHtml(label)}" role="checkbox"
            aria-checked="${selectedSet.has(label)}">
      <span class="facet__box">✓</span>
      <span class="facet__label">${escHtml(label)}</span>
      <span class="facet__count">${count.toLocaleString()}</span>
    </button>`).join('');

  if (collapsible) {
    const more = $('source-more');
    more.hidden = state.sourcesExpanded ? items.length <= LIMIT : hidden === 0;
    more.textContent = state.sourcesExpanded ? 'Show fewer' : `Show all sources (+${hidden})`;
  }
}

// ── Render list ────────────────────────────────────────────────────────
function renderRow(job, index) {
  const fresh    = dateGroupLabel(job.scraped_date) === 'Today';
  const selected = job.id === state.selected;
  const delay    = Math.min(index, 10) * 22;
  const showStamp = state.status === 'all' && job.status !== 'inbox';

  // Fit badge: pure LLM verdict/score — nothing shown until a job is
  // actually ranked (fit_bucket is 'Not yet scored' until then; see fit_sql).
  const fitBadge = job.llm_fit_score != null && job.fit_bucket && job.fit_bucket !== 'Neutral'
    ? `<span class="row__fit row__fit--${fitClass(job.fit_bucket)}">🤖 ${escHtml(job.fit_bucket)} ${job.llm_fit_score}</span>`
    : '';
  // Only meaningful once the German gate has been opened for review
  const langBadge = job.lang_bucket && job.lang_bucket !== 'No language barrier'
    ? `<span class="row__lang">${escHtml(job.lang_bucket)}</span>`
    : '';

  return `
  <div class="row${selected ? ' is-selected' : ''}" role="listitem" tabindex="-1"
       data-id="${job.id}" style="animation-delay:${delay}ms">
    <div class="row__top">
      <span class="row__title">${escHtml(job.title)}</span>
      <span class="row__age${fresh ? ' is-fresh' : ''}">${fresh ? '● ' : ''}${timeAgo(job.scraped_date)}</span>
    </div>
    <div class="row__sub">
      <span class="row__co">${escHtml(job.company || 'Unknown')}</span>
      <span class="row__dot">·</span>
      <span class="row__city">${escHtml(job.city)}</span>
      ${fitBadge}${langBadge}
      ${job.seniority ? `<span class="row__lvl ${lvlClass(job.seniority)}">${escHtml(job.seniority)}</span>` : ''}
      ${showStamp ? `<span class="row__stamp row__stamp--${job.status}">${job.status}</span>` : ''}
      <span class="row__src">${escHtml(job.source || '')}</span>
    </div>
    <div class="row__acts">
      ${job.status !== 'saved'   ? `<button class="qa qa--save"  data-act="saved"   data-id="${job.id}" title="Save (s)">save</button>` : ''}
      ${job.status !== 'applied' ? `<button class="qa qa--apply" data-act="applied" data-id="${job.id}" title="Mark applied (a)">applied</button>` : ''}
      ${job.status !== 'hidden'  ? `<button class="qa qa--hide"  data-act="hidden"  data-id="${job.id}" title="Hide (x)">hide</button>` : ''}
      ${state.status !== 'inbox' && job.status !== 'inbox' ? `<button class="qa" data-act="inbox" data-id="${job.id}" title="Back to inbox">↩ inbox</button>` : ''}
    </div>
  </div>`;
}

function renderList() {
  const list = $('job-list');

  // Grouping must follow the sort order, or separators fight the ranking:
  // fit sort groups by fit bucket, date sorts group by recency.
  const byFit  = state.sort === 'fit';
  const ORDER  = byFit
    ? ['Strong fit', 'Neutral', 'Weak fit', 'Not yet scored']
    : ['Today', 'Yesterday', 'This week', 'This month', 'Older'];
  const keyFn  = byFit
    ? j => j.fit_bucket || 'Neutral'
    : j => dateGroupLabel(j.scraped_date);

  const groups = {};
  state.jobs.forEach(j => {
    const l = keyFn(j);
    (groups[l] = groups[l] || []).push(j);
  });

  let i = 0;
  list.innerHTML = ORDER.filter(l => groups[l]).map(l =>
    `<div class="list__sep" role="separator">${l}</div>` +
    groups[l].map(j => renderRow(j, i++)).join('')
  ).join('');
}

function showEmptyState() {
  const empty = $('empty-state');
  empty.hidden = false;
  const titles = {
    inbox:   ['Inbox zero.', 'Every new listing has been triaged. Check back after tonight’s scrape.'],
    saved:   ['No saved jobs', 'Press s on a listing (or hover → save) to shortlist it here.'],
    applied: ['Nothing applied yet', 'Mark listings with a once you’ve sent an application.'],
    hidden:  ['Nothing hidden', 'Press x on listings you never want to see again.'],
    all:     ['No matches', 'Try different keywords or reset the filters.'],
  };
  const [t, b] = hasActiveFilters()
    ? ['No matches', 'Try different keywords or reset the filters.']
    : (titles[state.status] || titles.all);
  $('empty-title').textContent = t;
  $('empty-body').textContent  = b;
}

function renderPagination() {
  const pag = $('pagination');
  pag.hidden = state.pages <= 1;
  $('pag-info').textContent = `${state.page} / ${state.pages}`;
  $('prev-btn').disabled = state.page <= 1;
  $('next-btn').disabled = state.page >= state.pages;
}

function highlightSelected() {
  document.querySelectorAll('.row').forEach(el =>
    el.classList.toggle('is-selected', +el.dataset.id === state.selected));
}

// ── Detail ─────────────────────────────────────────────────────────────
function clearDetail() {
  state.selected = null;
  $('job-detail').innerHTML = '';
  $('detail-empty').style.display = '';
  $('workspace').classList.remove('is-detail-open');
}

async function selectJob(id, { scroll = true, openPanel = false } = {}) {
  const token = ++_selectToken;
  state.selected = id;
  highlightSelected();

  if (scroll) {
    const el = document.querySelector(`.row[data-id="${id}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }
  if (openPanel) $('workspace').classList.add('is-detail-open');

  $('detail-empty').style.display = 'none';

  let job = state.jobs.find(j => j.id === id);
  if (!job || job.description === undefined) {
    try {
      job = await (await fetch(`/api/jobs/${id}?profile=${encodeURIComponent(state.profile)}`)).json();
    } catch { return; }
  }
  if (_selectToken !== token) return;
  renderDetail(job);
}

function renderDetail(job) {
  const desc   = cleanDescription(job.description);
  const posted = job.posted_date ? timeAgo(job.posted_date) : null;
  const el     = $('job-detail');

  el.innerHTML = `
    <div class="dt__kicker">
      <span>${escHtml(job.source || 'listing')}</span>
      <span>№ ${job.id}</span>
    </div>
    <h1 class="dt__title">${escHtml(job.title)}</h1>
    <p class="dt__co"><strong>${escHtml(job.company || 'Unknown company')}</strong>
      &nbsp;—&nbsp; ${escHtml(job.location || 'location unknown')}</p>

    <div class="dt__actions">
      <a class="btn btn--primary" href="${escHtml(job.url)}" target="_blank" rel="noopener noreferrer">
        Open &amp; apply <kbd>o</kbd>
      </a>
      <button class="btn btn--ghost is-disabled" aria-disabled="true" title="Out of tokens">
        Auto Apply
      </button>
      <button class="btn btn--ghost${job.status === 'saved' ? ' is-saved' : ''}"
              data-act="${job.status === 'saved' ? 'inbox' : 'saved'}" data-id="${job.id}">
        ${job.status === 'saved' ? '★ Saved' : '☆ Save'} <kbd>s</kbd>
      </button>
      <button class="btn btn--ghost${job.status === 'applied' ? ' is-applied' : ''}"
              data-act="${job.status === 'applied' ? 'inbox' : 'applied'}" data-id="${job.id}">
        ${job.status === 'applied' ? '✓ Applied' : 'Mark applied'} <kbd>a</kbd>
      </button>
      <button class="btn btn--ghost" data-act="${job.status === 'hidden' ? 'inbox' : 'hidden'}" data-id="${job.id}">
        ${job.status === 'hidden' ? 'Unhide' : 'Hide'} <kbd>x</kbd>
      </button>
    </div>

    <div class="dt__facts">
      <div class="fact"><div class="fact__k">Fit</div>
        <div class="fact__v fact__v--${fitClass(job.fit_bucket)}">${escHtml(job.fit_bucket || 'Not yet scored')}${job.llm_fit_score != null ? ' · ' + job.llm_fit_score : ''}</div></div>
      <div class="fact"><div class="fact__k">City</div><div class="fact__v">${escHtml(job.city)}</div></div>
      <div class="fact"><div class="fact__k">Level</div><div class="fact__v">${escHtml(job.seniority || '—')}</div></div>
      <div class="fact"><div class="fact__k">Scraped</div><div class="fact__v">${escHtml(timeAgo(job.scraped_date) || '—')}</div></div>
      <div class="fact"><div class="fact__k">Posted</div><div class="fact__v">${escHtml(posted || 'unknown')}</div></div>
    </div>

    ${job.llm_fit_dims ? llmSection(job) : `
      <div class="dt__section-title">Why this fit score</div>
      <p class="dt__nodesc">Not yet scored${desc ? '' : ' — no description was captured either, so there may be little to score once it is'}.</p>
    `}

    <div class="dt__section-title">About the role</div>
    ${desc
      ? `<div class="dt__desc">${escHtml(desc)}</div>`
      : `<p class="dt__nodesc">No description captured for this listing — open the original for the full text.</p>`}

    <div class="dt__footer">
      <a class="dt__srclink" href="${escHtml(job.url)}" target="_blank" rel="noopener noreferrer">
        View original on ${escHtml(job.source || 'source')} ↗
      </a>
    </div>`;

  $('detail-pane').scrollTo({ top: 0 });
}

// ── Status changes (optimistic + undo) ─────────────────────────────────
async function setStatus(id, newStatus, { silent = false } = {}) {
  const job = state.jobs.find(j => j.id === id);
  const prevStatus = job ? job.status : null;
  if (prevStatus === newStatus) return;

  if (job) job.status = newStatus;

  // Does the job leave the current tab?
  const leaves = state.status !== 'all' && newStatus !== state.status;

  if (leaves) {
    const rowEl = document.querySelector(`.row[data-id="${id}"]`);
    if (rowEl) {
      rowEl.classList.add('is-leaving');
      setTimeout(() => {
        state.jobs = state.jobs.filter(j => j.id !== id);
        const wasSelected = state.selected === id;
        renderList();
        if (wasSelected) {
          // advance selection to the next row so triage keeps flowing
          const next = $('job-list').querySelector('.row');
          if (next && !isMobileDetail()) selectJob(+next.dataset.id, { scroll: false });
          else clearDetail();
        } else {
          highlightSelected();
        }
        state.total -= 1;
        $('results-count').textContent =
          `${state.total.toLocaleString()} listing${state.total === 1 ? '' : 's'}`;
        if (state.jobs.length === 0) showEmptyState();
      }, 230);
    }
  } else {
    renderList();
    highlightSelected();
    if (state.selected === id) selectJob(id, { scroll: false });
  }

  try {
    await fetch(`/api/jobs/${id}/status?profile=${encodeURIComponent(state.profile)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    });
  } catch {
    showToast('Could not reach the server — change not saved', null);
    return;
  }

  loadFacets(); // refresh tab counts

  if (!silent) {
    _lastAction = { id, prevStatus: prevStatus || 'inbox' };
    const verbs = { saved: 'Saved', applied: 'Marked applied', hidden: 'Hidden', inbox: 'Moved to inbox' };
    showToast(`${verbs[newStatus] || 'Updated'} — ${escHtml((job && job.title) || 'listing').slice(0, 48)}`,
              _lastAction, { askWhy: newStatus === 'hidden' ? id : null });
  }
}

function undoLast() {
  if (!_lastAction) return;
  const { id, prevStatus } = _lastAction;
  _lastAction = null;
  hideToast();
  fetch(`/api/jobs/${id}/status?profile=${encodeURIComponent(state.profile)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: prevStatus }),
  }).then(() => refresh({ keepSelection: true }));
}

function showToast(msg, action, { askWhy = null } = {}) {
  const toast = $('toast');
  $('toast-msg').textContent = msg;
  $('toast-undo').style.display = action ? '' : 'none';

  const why = $('toast-why');
  if (askWhy) {
    // Asking is optional by design: an unanswered hide is still a negative
    // signal, so this must never block the next keystroke of triage.
    why.innerHTML = '<span class="toast__why-q">Why?</span>' +
      _hideReasons.map(r =>
        `<button class="toast__why-btn" data-id="${askWhy}" data-reason="${escHtml(r)}">${escHtml(r)}</button>`
      ).join('') +
      `<button class="toast__why-btn" data-id="${askWhy}" data-reason="__other__">other…</button>`;
    why.hidden = false;
  } else {
    why.hidden = true;
    why.innerHTML = '';
  }

  toast.hidden = false;
  clearTimeout(_toastTimer);
  // A reason row needs time to read and click; a bare confirmation does not.
  _toastTimer = setTimeout(hideToast, askWhy ? 9000 : 5000);
}
function hideToast() { $('toast').hidden = true; }

async function recordHideReason(id, reason) {
  if (reason === '__other__') {
    reason = (window.prompt('Why are you hiding this one?') || '').trim();
    if (!reason) return;
  }
  const why = $('toast-why');
  why.innerHTML = '<span class="toast__why-q">Noted — future scoring will weigh this.</span>';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(hideToast, 2200);
  try {
    await fetch(`/api/jobs/${id}/hide-reason?profile=${encodeURIComponent(state.profile)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
  } catch {
    why.innerHTML = '<span class="toast__why-q">Could not save that reason.</span>';
  }
}

// ── Filters / tabs ─────────────────────────────────────────────────────
function switchTab(status) {
  if (state.status === status) return;
  state.status = status;
  state.page = 1;
  document.querySelectorAll('.tabs__tab').forEach(t => {
    const active = t.dataset.status === status;
    t.classList.toggle('is-active', active);
    t.setAttribute('aria-selected', active);
  });
  refresh();
}

function toggleFacet(dim, value) {
  const set = state[dim];
  set.has(value) ? set.delete(value) : set.add(value);
  state.page = 1;
  refresh();
}

function clearAllFilters() {
  state.q = '';
  state.source.clear();
  state.seniority.clear();
  state.city.clear();
  state.fit.clear();
  state.lang.clear();
  state.days = 0;
  state.page = 1;
  $('search-input').value = '';
  document.querySelectorAll('#facet-days .seg').forEach((s, i) =>
    s.classList.toggle('is-active', i === 0));
  refresh();
}

function closeDetail() {
  $('workspace').classList.remove('is-detail-open');
}

// ── Keyboard ───────────────────────────────────────────────────────────
function currentIndex() {
  return state.jobs.findIndex(j => j.id === state.selected);
}

function moveSelection(delta) {
  if (!state.jobs.length) return;
  const idx  = currentIndex();
  const next = idx === -1 ? 0 : Math.max(0, Math.min(state.jobs.length - 1, idx + delta));
  selectJob(state.jobs[next].id, { openPanel: isMobileDetail() ? false : undefined });
}

function handleKey(e) {
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
    if (e.key === 'Escape') document.activeElement.blur();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  const sel = state.jobs.find(j => j.id === state.selected);

  switch (e.key) {
    case 'j': case 'ArrowDown': e.preventDefault(); moveSelection(1);  break;
    case 'k': case 'ArrowUp':   e.preventDefault(); moveSelection(-1); break;
    case 's': if (sel) setStatus(sel.id, sel.status === 'saved' ? 'inbox' : 'saved'); break;
    case 'a': if (sel) setStatus(sel.id, sel.status === 'applied' ? 'inbox' : 'applied'); break;
    case 'x': if (sel) setStatus(sel.id, sel.status === 'hidden' ? 'inbox' : 'hidden'); break;
    case 'o': case 'Enter':
      if (sel) window.open(sel.url, '_blank', 'noopener');
      break;
    case 'u': undoLast(); break;
    case '/': e.preventDefault(); $('search-input').focus(); break;
    case '[': if (state.page > 1)          { state.page--; loadJobs(); } break;
    case ']': if (state.page < state.pages) { state.page++; loadJobs(); } break;
    case '1': switchTab('inbox');   break;
    case '2': switchTab('saved');   break;
    case '3': switchTab('applied'); break;
    case '4': switchTab('hidden');  break;
    case '5': switchTab('all');     break;
    case 'Escape': closeDetail(); break;
  }
}

// ── One-time migration: localStorage saves → server status ─────────────
async function migrateLocalSaves() {
  const raw = localStorage.getItem('jf_saved');
  if (!raw) return;
  try {
    const ids = JSON.parse(raw);
    await Promise.all(ids.map(id => fetch(`/api/jobs/${id}/status?profile=${encodeURIComponent(state.profile)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'saved' }),
    }).catch(() => null)));
  } finally {
    localStorage.removeItem('jf_saved');
  }
}

// ── Profile switching ─────────────────────────────────────────────────
function renderProfileMenu() {
  const menu = $('profile-menu');
  menu.innerHTML = Object.entries(state.profiles).map(([key, p]) =>
    `<button class="hd__profile-opt${key === state.profile ? ' is-active' : ''}"
             role="menuitem" data-profile="${escHtml(key)}">${escHtml(p.label)}</button>`
  ).join('');
  $('profile-name').textContent = (state.profiles[state.profile] || {}).short || 'PM';
}

function toggleProfileMenu(force) {
  const btn  = $('profile-btn');
  const menu = $('profile-menu');
  const open = force !== undefined ? force : !menu.classList.contains('is-open');
  menu.classList.toggle('is-open', open);
  btn.setAttribute('aria-expanded', open);
}

function switchProfile(key) {
  if (key === state.profile) { toggleProfileMenu(false); return; }
  state.profile = key;
  setCookie('jf_profile', key);
  toggleProfileMenu(false);
  renderProfileMenu();
  clearAllFilters();
}

// ── Scrape ────────────────────────────────────────────────────────────
function startScrape() {
  const btn = $('scrape-btn');
  if (btn.classList.contains('is-running')) return;
  btn.classList.add('is-running');
  $('scrape-label').textContent = 'Scraping…';

  fetch('/api/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile: state.profile }),
  }).then(r => r.json()).then(data => {
    if (data.error) {
      showToast(data.error, null);
      btn.classList.remove('is-running');
      $('scrape-label').textContent = 'Scrape';
      return;
    }
    _scrapePoll = setInterval(pollScrape, 3000);
  }).catch(() => {
    showToast('Could not start scrape — is the server running?', null);
    btn.classList.remove('is-running');
    $('scrape-label').textContent = 'Scrape';
  });
}

function pollScrape() {
  fetch('/api/scrape/status').then(r => r.json()).then(s => {
    if (!s.running && s.result) {
      clearInterval(_scrapePoll);
      _scrapePoll = null;
      $('scrape-btn').classList.remove('is-running');
      $('scrape-label').textContent = 'Scrape';
      if (s.result.ok) {
        showToast(`Scrape done — ${s.result.added} new job${s.result.added === 1 ? '' : 's'}`, null);
      } else {
        showToast(`Scrape failed: ${s.result.error}`, null);
      }
      refresh();
    }
  }).catch(() => {});
}

// ── Bootstrap ──────────────────────────────────────────────────────────
async function init() {
  // load profiles, resolve active profile from cookie
  try {
    const pdata = await (await fetch('/api/profiles')).json();
    state.profiles = pdata.profiles;
    const saved = getCookie('jf_profile');
    state.profile = state.profiles[saved] ? saved : pdata.default;
  } catch {
    state.profile = '';
  }

  renderProfileMenu();
  await migrateLocalSaves();

  // One source of truth for hide reasons — falls back to the local list.
  fetch(`/api/preferences?profile=${encodeURIComponent(state.profile)}`)
    .then(r => r.json())
    .then(p => { if (p && p.hide_reasons && p.hide_reasons.length) _hideReasons = p.hide_reasons; })
    .catch(() => {});

  // profile dropdown
  $('profile-btn').addEventListener('click', () => toggleProfileMenu());
  $('profile-menu').addEventListener('click', e => {
    const opt = e.target.closest('[data-profile]');
    if (opt) switchProfile(opt.dataset.profile);
  });
  document.addEventListener('click', e => {
    if (!$('profile-dropdown').contains(e.target)) toggleProfileMenu(false);
  });

  // scrape button
  $('scrape-btn').addEventListener('click', startScrape);

  // check if a scrape is already in progress (e.g. page reload mid-scrape)
  fetch('/api/scrape/status').then(r => r.json()).then(s => {
    if (s.running) {
      $('scrape-btn').classList.add('is-running');
      $('scrape-label').textContent = 'Scraping…';
      _scrapePoll = setInterval(pollScrape, 3000);
    }
  }).catch(() => {});

  // tabs
  document.querySelectorAll('.tabs__tab').forEach(t =>
    t.addEventListener('click', () => switchTab(t.dataset.status)));

  // freshness segments
  document.querySelectorAll('#facet-days .seg').forEach(s =>
    s.addEventListener('click', () => {
      document.querySelectorAll('#facet-days .seg').forEach(x => x.classList.remove('is-active'));
      s.classList.add('is-active');
      state.days = +s.dataset.days;
      state.page = 1;
      refresh();
    }));

  // facet clicks (delegated)
  $('rail').addEventListener('click', e => {
    const f = e.target.closest('.facet');
    if (f) toggleFacet(f.dataset.dim, f.dataset.value);
  });
  $('source-more').addEventListener('click', () => {
    state.sourcesExpanded = !state.sourcesExpanded;
    loadFacets();
  });
  $('clear-filters').addEventListener('click', clearAllFilters);

  // list clicks (delegated): quick actions + row select
  $('job-list').addEventListener('click', e => {
    const qa = e.target.closest('[data-act]');
    if (qa) { e.stopPropagation(); setStatus(+qa.dataset.id, qa.dataset.act); return; }
    const row = e.target.closest('.row');
    if (row) selectJob(+row.dataset.id, { openPanel: true });
  });

  // detail action buttons (delegated — content re-renders)
  $('job-detail').addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (b) setStatus(+b.dataset.id, b.dataset.act);
  });

  // search
  $('search-input').addEventListener('input', debounce(e => {
    state.q    = e.target.value.trim();
    state.page = 1;
    refresh();
  }, 300));

  // sort
  $('sort-select').addEventListener('change', e => {
    state.sort = e.target.value;
    state.page = 1;
    loadJobs();
  });

  // pagination
  $('prev-btn').addEventListener('click', () => {
    if (state.page > 1) { state.page--; loadJobs(); $('list-scroll').scrollTo({ top: 0 }); }
  });
  $('next-btn').addEventListener('click', () => {
    if (state.page < state.pages) { state.page++; loadJobs(); $('list-scroll').scrollTo({ top: 0 }); }
  });

  // mobile filter drawer
  $('filters-toggle').addEventListener('click', () => {
    const ws   = $('workspace');
    const open = ws.classList.toggle('is-rail-open');
    $('filters-toggle').setAttribute('aria-expanded', open);
  });

  // undo toast
  $('toast-undo').addEventListener('click', undoLast);

  $('toast-why').addEventListener('click', e => {
    const btn = e.target.closest('[data-reason]');
    if (btn) recordHideReason(+btn.dataset.id, btn.dataset.reason);
  });
  // Deciding on a reason should not race the auto-dismiss timer.
  $('toast').addEventListener('mouseenter', () => clearTimeout(_toastTimer));
  $('toast').addEventListener('mouseleave', () => {
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(hideToast, 2500);
  });

  document.addEventListener('keydown', handleKey);

  // if the window grows from overlay to side-by-side, fill the empty pane
  window.addEventListener('resize', debounce(() => {
    if (!isMobileDetail() && !state.selected && state.jobs.length) {
      selectJob(state.jobs[0].id, { scroll: false });
    }
  }, 200));

  refresh();
}

document.addEventListener('DOMContentLoaded', init);
