"""Arbeitnow scraper — free public REST API, filters locally by location and role."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

_API = "https://www.arbeitnow.com/api/job-board-api"
_REQ_TIMEOUT = 10    # per request; the API stalls when throttling
_TIME_BUDGET = 90    # seconds for the whole source


def _matches_location(location: str, tags: list, keywords: list[str]) -> bool:
    loc = location.lower()
    return (
        any(kw in loc for kw in keywords)
        or any(kw in str(t).lower() for kw in keywords for t in tags)
    )


def _parse_date(created_at) -> str | None:
    if isinstance(created_at, int):
        return datetime.fromtimestamp(created_at).date().isoformat()
    if isinstance(created_at, str) and created_at:
        return created_at[:10]
    return None


def fetch(template: JobSearchTemplate) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    page = 1
    deadline = time.monotonic() + _TIME_BUDGET

    while True:
        # The API throttles by stalling rather than 429-ing once it has seen a
        # few runs, so a per-request timeout alone doesn't bound the scraper.
        # This source yields ~10 jobs; it does not get to cost 30 minutes.
        if time.monotonic() > deadline:
            logger.info("  Arbeitnow: time budget reached, keeping %d jobs", len(jobs))
            break
        try:
            resp = requests.get(_API, params={"page": page}, timeout=_REQ_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException:
            # Rate-limited mid-pagination (429) — keep the pages already fetched
            break
        data = resp.json()
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            url: str = item.get("url", "")
            if not url or url in seen:
                continue

            title: str = item.get("title", "")
            description: str = item.get("description", "")
            tags: list = item.get("tags", [])
            location: str = item.get("location", "")

            if not _matches_location(location, tags, template.location_keywords):
                continue
            if not is_product_role(title) or is_excluded(title, description):
                continue
            posted_date = _parse_date(item.get("created_at"))
            if not is_recent(posted_date):
                continue

            seen.add(url)
            remote: bool = bool(item.get("remote", False))

            jobs.append(Job(
                title=title,
                company=item.get("company_name", ""),
                location=location or template.location,
                url=url,
                source="Arbeitnow",
                seniority=classify_seniority(title, description),
                posted_date=posted_date,
                description=description,
            ))

        links = data.get("links", {})
        if not links.get("next"):
            break
        page += 1
        if page > 20:
            break

    return jobs
