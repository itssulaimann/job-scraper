"""
LinkedIn guest job-detail fetcher — free, no auth, no key.

JobSpy returns LinkedIn jobs with an EMPTY description, because its
`linkedin_fetch_description` option fetches `/jobs/view/{id}`, which now sits
behind an auth wall (JobSpy issue #374). Without a description, both
`is_excluded()` and `classify_seniority()` end up running on an empty string,
so every LinkedIn job falls through to `seniority_default`.

The guest endpoint still serves the full posting anonymously:

    https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}

It returns an HTML fragment containing the description plus a criteria list
(Seniority level, Employment type, Job function, Industries).

Rate limits (measured): the search endpoint tolerates ~1.2s spacing, but this
detail endpoint is more sensitive and wants ~3s. Enrichment is therefore both
throttled and capped — see `enrich_many`.
"""
from __future__ import annotations

import html
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Politeness delay between detail calls. The endpoint 429s below ~3s.
_DELAY = 3.0
_TIMEOUT = 15

_JOB_ID_RE = re.compile(r"/jobs/view/(?:.*-)?(\d{6,})")
_CRITERIA_RE = re.compile(
    r'job-criteria-subheader"?>\s*([^<]+?)\s*</h3>\s*'
    r'<span[^>]*job-criteria-text[^>]*>\s*([^<]+?)\s*</span>',
    re.DOTALL,
)
_SECTION_RE = re.compile(
    r'<div[^>]*class="[^"]*(?:show-more-less-html__markup|description__text)[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)


def extract_job_id(url: str) -> str | None:
    """Pull the numeric LinkedIn job ID out of a /jobs/view/ URL."""
    if not url:
        return None
    m = _JOB_ID_RE.search(url)
    return m.group(1) if m else None


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment, flags=re.DOTALL | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]*\n[ \t]*", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def fetch_detail(job_id: str, session: requests.Session | None = None) -> dict | None:
    """
    Fetch one job's detail fragment.

    Returns {"description": str, "criteria": {label: value}} or None on failure.
    """
    sess = session or requests
    try:
        r = sess.get(_DETAIL.format(job_id=job_id), headers=_HEADERS, timeout=_TIMEOUT)
    except Exception as e:
        logger.debug("LinkedIn guest detail %s: %s", job_id, e)
        return None

    if r.status_code == 429:
        logger.debug("LinkedIn guest detail %s: 429 rate limited", job_id)
        return None
    if r.status_code != 200 or not r.text:
        logger.debug("LinkedIn guest detail %s: HTTP %s", job_id, r.status_code)
        return None

    criteria = {
        html.unescape(k).strip(): html.unescape(v).strip()
        for k, v in _CRITERIA_RE.findall(r.text)
    }

    section = _SECTION_RE.search(r.text)
    description = _strip_html(section.group(1)) if section else _strip_html(r.text)

    if not description:
        return None
    return {"description": description, "criteria": criteria}


def enrich_many(
    urls: list[str],
    max_jobs: int = 60,
    delay: float = _DELAY,
) -> dict[str, dict]:
    """
    Fetch details for a list of LinkedIn job URLs, throttled and capped.

    Capped at `max_jobs` to bound both wall-clock time and request volume —
    at the default 3s delay, 60 jobs costs ~3 minutes. Callers should pass only
    URLs for jobs that already passed the cheap title-based filter, so no
    request is spent on a job that will be discarded anyway.

    Returns {url: {"description": ..., "criteria": {...}}} for successful fetches.
    """
    out: dict[str, dict] = {}
    if not urls:
        return out

    targets = urls[:max_jobs]
    if len(urls) > max_jobs:
        logger.info("    LinkedIn enrich: capped at %d of %d candidates",
                    max_jobs, len(urls))

    session = requests.Session()
    consecutive_failures = 0

    for i, url in enumerate(targets):
        job_id = extract_job_id(url)
        if not job_id:
            continue

        detail = fetch_detail(job_id, session=session)
        if detail:
            out[url] = detail
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            # Endpoint has started refusing us — stop rather than burn requests.
            if consecutive_failures >= 5:
                logger.info("    LinkedIn enrich: 5 consecutive failures, stopping early "
                            "(%d/%d enriched)", len(out), len(targets))
                break

        if i < len(targets) - 1:
            time.sleep(delay)

    logger.info("    LinkedIn enrich: %d/%d descriptions fetched", len(out), len(targets))
    return out
