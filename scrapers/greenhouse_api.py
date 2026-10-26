"""
Greenhouse JSON API scraper — free, no Apify required.

Uses the public Greenhouse boards API directly:
  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

This replaces the Apify-based greenhouse.py for companies whose job boards
are hosted on Greenhouse. The API is completely free, requires no key, and
returns full job descriptions in one call.

Add company slugs to template.greenhouse_slugs — the slug is the path segment
after boards.greenhouse.io/ (e.g. "hellofresh", "n26", "sumup").
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_API_US = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_API_EU = "https://api.eu.greenhouse.io/v1/boards/{slug}/jobs"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (JobFinder)"})


# ── HTML → plain text ─────────────────────────────────────────────────────
class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    if not html:
        return ""
    s = _Stripper()
    s.feed(html)
    return " ".join(s.get_text().split())  # collapse whitespace


def _location_matches(location: str, keywords: list[str]) -> bool:
    """Keep only postings in the template's region; no keywords means keep all."""
    if not keywords:
        return True
    loc = (location or "").lower()
    return any(kw.lower() in loc for kw in keywords)


def _parse_date(published) -> str | None:
    if isinstance(published, (int, float)) and published > 0:
        return datetime.fromtimestamp(published / 1000, tz=timezone.utc).date().isoformat()
    if isinstance(published, str) and published:
        return published[:10]
    return None


# ── Core fetch ────────────────────────────────────────────────────────────
def fetch(template: "JobSearchTemplate") -> list[Job]:
    """
    Fetch jobs from all Greenhouse boards listed in template.greenhouse_slugs.
    Returns jobs that pass role + recency filters.
    """
    slugs: list[str] = template.greenhouse_slugs or []
    if not slugs:
        return []

    jobs:  list[Job] = []
    seen:  set[str]  = set()
    total_fetched = 0

    for slug in slugs:
        r = None
        for endpoint in (_API_US, _API_EU):
            url = endpoint.format(slug=slug)
            try:
                resp = _SESSION.get(url, params={"content": "true"}, timeout=15)
                resp.raise_for_status()
                r = resp
                break
            except requests.HTTPError:
                continue
            except Exception as e:
                logger.debug("Greenhouse API: %s → %s", slug, e)
                break
        if r is None:
            logger.debug("Greenhouse API: %s → not found on US or EU endpoint", slug)
            continue

        try:
            payload = r.json()
        except Exception:
            logger.debug("Greenhouse API: %s → empty or invalid JSON response", slug)
            continue
        items = payload.get("jobs", [])
        total_fetched += len(items)

        for item in items:
            job_url = item.get("absolute_url", "")
            if not job_url or job_url in seen:
                continue

            title       = item.get("title", "")
            description = _strip_html(item.get("content", ""))
            posted_date = _parse_date(item.get("first_published") or item.get("updated_at"))

            if not title:
                continue
            if not is_product_role(title) or is_excluded(title, description):
                continue
            # 90 days, not 30: a Greenhouse board only lists roles that are
            # still open, so an old first_published date means "posted a while
            # ago and still hiring", not "stale listing". A 30-day window was
            # silently dropping live roles (e.g. NavVis and Kinexon PM openings
            # first published in Q2 but updated within the last week).
            if not is_recent(posted_date, max_hours=90 * 24):
                continue

            # Location: Greenhouse returns {"name": "Berlin, Germany"} or a string
            loc_raw  = item.get("location", {})
            location = (loc_raw.get("name") if isinstance(loc_raw, dict) else str(loc_raw or "")) \
                       or template.location

            # Greenhouse boards are global — a company headquartered in one city
            # still lists roles worldwide. Without this the Munich template pulls
            # in Berlin, Madrid and Toronto postings from shared slugs.
            if not _location_matches(location, template.location_keywords):
                continue

            # Company name from slug (Greenhouse API doesn't return it in the job list)
            # Use title-case of the slug as a fallback; notion_writer will do a company lookup
            company_guess = slug.replace("-", " ").title()

            seen.add(job_url)
            jobs.append(Job(
                title=title,
                company=company_guess,
                location=location,
                url=job_url,
                source="Greenhouse",
                seniority=classify_seniority(title, description),
                posted_date=posted_date,
                description=description,
            ))

    logger.debug("Greenhouse API: %d total fetched, %d PM matched", total_fetched, len(jobs))
    return jobs
