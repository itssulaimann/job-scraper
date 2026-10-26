"""
VC portfolio job boards — free, keyless, via Getro's public search API.

Getro powers the "jobs" board of many European VCs. Each board is a numbered
*collection* (network) aggregating every portfolio company's live openings, so
one request covers a whole portfolio without maintaining per-company ATS slugs.
That is the point of this scraper: company discovery. The `url` on each hit
points at the underlying ATS posting (Ashby/Greenhouse/Lever/…), so anything
also found by the per-slug scrapers dedups naturally on URL hash.

API notes, all established by probing (it is undocumented):

    POST https://api.getro.com/api/v2/collections/{network_id}/search/jobs
    Accept: application/json          — omitting this returns 406, not 400
    {"hitsPerPage": 20, "page": 0, "query": "...",
     "filters": {"searchable_locations": ["Germany"]}}

  * `searchable_locations` is the filter key that actually constrains results.
    `locations` and `job_locations` are accepted but silently ignored — they
    return the unfiltered set, which looks like success.
  * hitsPerPage is capped at 20 server-side regardless of what is requested.
  * Descriptions require an authenticated key (401), so jobs land here with an
    empty description and are classified on title alone.

Finding a network id: load the board (e.g. https://cherry.getro.com) and read
the numeric id out of the embedded JSON.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_API = "https://api.getro.com/api/v2/collections/{network_id}/search/jobs"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; JobFinder/1.0)",
}
_TIMEOUT = 25
_PAGE_SIZE = 20          # server-side cap
_MAX_PAGES = 8           # 160 hits per query per board
_RECENT_HOURS = 60 * 24  # portfolio boards keep postings up for weeks


def _country_name(code: str) -> str:
    return {"DE": "Germany", "AT": "Austria", "CH": "Switzerland"}.get(
        (code or "DE").upper(), "Germany"
    )


def _posted_date(created_at) -> str | None:
    if not isinstance(created_at, (int, float)) or created_at <= 0:
        return None
    try:
        return datetime.fromtimestamp(created_at, tz=timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _location_ok(locations: list, keywords: list[str], country: str) -> str | None:
    """
    Keep a job when any of its offices is in the template's city, or when it is
    listed against the country as a whole — that is how remote-DE roles appear,
    and the profile treats remote-Germany as in scope.
    """
    for loc in locations or []:
        text = str(loc)
        low = text.lower()
        if any(kw.lower() in low for kw in keywords):
            return text
        if low.strip() == country.lower():
            return text
    return None


def _search(session: requests.Session, network_id, query: str, country: str) -> list[dict]:
    hits: list[dict] = []
    for page in range(_MAX_PAGES):
        payload = {
            "hitsPerPage": _PAGE_SIZE,
            "page": page,
            "query": query,
            "filters": {"searchable_locations": [country]},
        }
        resp = session.post(
            _API.format(network_id=network_id),
            json=payload, headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or {}
        batch = results.get("jobs") or []
        hits.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
    return hits


def fetch(template: "JobSearchTemplate") -> list[Job]:
    networks = getattr(template, "getro_networks", None) or []
    if not networks:
        return []

    queries = getattr(template, "getro_queries", None) or [template.indeed_position]
    queries = [q for q in queries if q]
    country = _country_name(template.country_code)

    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()

    for net in networks:
        name = net.get("name") or str(net.get("id"))
        network_id = net.get("id")
        if not network_id:
            continue

        found = 0
        for query in queries:
            try:
                hits = _search(session, network_id, query, country)
            except Exception as e:
                logger.debug("Getro %s (%s) query %r: %s", name, network_id, query, e)
                continue

            for item in hits:
                url = (item.get("url") or "").strip()
                title = (item.get("title") or "").strip()
                if not url or not title or url in seen:
                    continue

                match = _location_ok(
                    item.get("locations"), template.location_keywords, country
                )
                if match is None:
                    continue

                if not is_product_role(title) or is_excluded(title):
                    continue

                posted_date = _posted_date(item.get("created_at"))
                if not is_recent(posted_date, max_hours=_RECENT_HOURS):
                    continue

                seen.add(url)
                found += 1
                jobs.append(Job(
                    title=title,
                    company=(item.get("organization") or {}).get("name") or name,
                    location=match or template.location,
                    url=url,
                    source="VC Board",
                    seniority=classify_seniority(title),
                    posted_date=posted_date,
                    description="",
                ))

        if found:
            logger.info("    %s → %d jobs", name, found)

    logger.debug("VC boards: %d jobs", len(jobs))
    return jobs
