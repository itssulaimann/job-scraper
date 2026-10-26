"""
JSearch scraper — Google for Jobs aggregate (LinkedIn, Indeed, Glassdoor, etc.).

    GET https://api.openwebninja.com/jsearch/search-v2
    Header: X-API-Key: <JSEARCH_API_KEY>

This is the one metered source in the pipeline, so it is written to be frugal.

QUOTA MODEL — read before changing anything here:
  Each *page* of up to 10 results costs one request credit, and `num_pages`
  multiplies that (num_pages=20 burns 20 credits in a single call). The free
  tier is 200 credits/month.

  Budget: 2 cities x 2 queries x _NUM_PAGES(2) = 8 credits per full run.
  At one run/day that is ~240/month — over the free cap. At the current
  every-other-day schedule it is ~120/month, comfortably inside it.

  `_MAX_CREDITS_PER_RUN` is a hard stop so a template with many queries can
  never silently drain the month's quota in one run.

Notes:
  - `country` MUST be set or you get US results. Do NOT also set `language`:
    for country=de it yields zero results (the docs warn about unsupported
    country/language pairs).
  - `job_city`/`job_state`/`job_country` are usually null; the human-readable
    location lives in `job_location`, formatted "Berlin  •  über LinkedIn".
  - `job_posted_at` is a localised relative string ("vor 16 Stunden"), so use
    `job_posted_at_datetime_utc` for the real date.
  - Descriptions are fully populated, which is why this source classifies well.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_API = "https://api.openwebninja.com/jsearch/search-v2"
_TIMEOUT = 30

# Pages fetched per query, one request each. Each page = 10 results = 1 credit.
#
# Deliberately NOT done via the API's own `num_pages` parameter: asking the
# server for 2+ pages in a single call reliably times out with a 504 for
# broader queries ("product manager in berlin" fails at 30s, while the same
# query with one page returns in 9s). Walking the cursor one page at a time
# costs exactly the same number of credits and actually succeeds.
_PAGES_PER_QUERY = 2

# Hard ceiling on credits spent in a single run, regardless of query count.
_MAX_CREDITS_PER_RUN = 8

# Retries for transient upstream failures (the gateway 504s intermittently).
# Failed requests are not billed, so this costs no credits.
_MAX_ATTEMPTS = 3
_RETRY_WAIT = 5

# Trailing publisher attribution in job_location, e.g. "Berlin  •  über LinkedIn"
_VIA_RE = re.compile(r"\s*[•·]\s*(?:über|via)\s+.*$", re.IGNORECASE)


def _clean_location(raw: str) -> str:
    return _VIA_RE.sub("", raw or "").strip()


def _parse_date(raw: str) -> str | None:
    """`job_posted_at_datetime_utc` looks like '2026-08-01T16:00:00.000Z'."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return raw[:10] if len(raw) >= 10 else None


def fetch(template: "JobSearchTemplate") -> list[Job]:
    """
    Query JSearch for each entry in `template.jsearch_queries`.

    Queries are free-form and should name both role and city, e.g.
    "product manager in berlin". Skipped entirely when no API key is set.
    """
    api_key = os.getenv("JSEARCH_API_KEY", "").strip()
    if not api_key:
        logger.info("JSearch: no JSEARCH_API_KEY in .env — skipping")
        return []

    queries = getattr(template, "jsearch_queries", None) or []
    if not queries:
        logger.info("JSearch: no jsearch_queries configured — skipping")
        return []

    country = (template.country_code or "DE").lower()
    jobs: list[Job] = []
    seen: set[str] = set()
    credits_spent = 0
    session = requests.Session()

    quota_hit = False

    for query in queries:
        if quota_hit:
            break
        cursor = None

        for page in range(_PAGES_PER_QUERY):
            if credits_spent + 1 > _MAX_CREDITS_PER_RUN:
                logger.info("JSearch: credit cap (%d) reached — stopping",
                            _MAX_CREDITS_PER_RUN)
                quota_hit = True
                break

            # No `fields` projection: it only trims the payload and does not
            # reduce credit cost, and the API 400s on some field names. Not
            # worth spending metered requests to discover which.
            params = {
                "query": query,
                "country": country,
                "date_posted": "week",
                "num_pages": 1,
            }
            if cursor:
                params["cursor"] = cursor

            # The upstream gateway intermittently 504s. Retry those — a
            # transient blip otherwise costs the whole source for the run.
            # A failed request is not billed, so retrying costs no credits.
            payload = None
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    r = session.get(_API, params=params, timeout=_TIMEOUT,
                                    headers={"X-API-Key": api_key})
                    if r.status_code == 429:
                        logger.warning("JSearch: quota exhausted or rate limited — stopping")
                        quota_hit = True
                        break
                    r.raise_for_status()
                    payload = r.json()
                    break
                except Exception as e:
                    if attempt == _MAX_ATTEMPTS:
                        logger.warning("JSearch '%s' p%d failed after %d attempts: %s",
                                       query, page + 1, attempt, e)
                    else:
                        logger.info("    JSearch '%s' p%d attempt %d failed (%s) — retrying",
                                    query, page + 1, attempt, e)
                        time.sleep(_RETRY_WAIT)

            if quota_hit or payload is None:
                break

            credits_spent += 1

            if payload.get("status") != "OK":
                logger.debug("JSearch '%s': status %s", query, payload.get("status"))
                break

            data = payload.get("data") or {}
            items = data.get("jobs") or []

            for item in items:
                url = (item.get("job_apply_link") or "").strip()
                title = (item.get("job_title") or "").strip()
                if not url or not title or url in seen:
                    continue

                description = (item.get("job_description") or "").strip()
                if not is_product_role(title) or is_excluded(title, description):
                    continue

                posted_date = _parse_date(item.get("job_posted_at_datetime_utc") or "")
                if not is_recent(posted_date, max_hours=30 * 24):
                    continue

                seen.add(url)
                jobs.append(Job(
                    title=title,
                    company=(item.get("employer_name") or "").strip(),
                    location=_clean_location(item.get("job_location") or "") or template.location,
                    url=url,
                    source="JSearch",
                    seniority=classify_seniority(title, description),
                    posted_date=posted_date,
                    description=description,
                ))

            cursor = data.get("cursor")
            if not cursor or not items:
                break

    logger.info("  JSearch: %d jobs (%d/%d credits used this run)",
                len(jobs), credits_spent, _MAX_CREDITS_PER_RUN)
    return jobs
