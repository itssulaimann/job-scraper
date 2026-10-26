"""
Careerjet scraper — free public search API, no key, no registration required.

    http://public.api.careerjet.net/search?locale_code=de_DE&keywords=...

Strong German coverage: ~964 hits for "product manager" in Berlin and ~519 in
Munich (measured Aug 2026). Aggregates from many German boards, so it overlaps
partially with existing sources — the dedup layer handles that.

Two operational details that are easy to get wrong:

  1. A `Referer` header is REQUIRED, and its *value* is validated — an external
     domain such as https://github.com/... returns HTTP 401, while localhost is
     accepted. Override with CAREERJET_REFERER if you host this somewhere with
     a real domain registered against your publisher account.

  2. Result URLs are `jobviewtrack.com` redirects that are stable *per affiliate
     ID* but change if the affiliate ID changes. Since dedup keys off a hash of
     the URL, changing CAREERJET_AFFID would make every previously-seen job look
     new and re-add the entire back catalogue. Set it once and leave it alone.

Careerjet does not validate the affiliate ID, but registering a real (free)
publisher ID at https://www.careerjet.com/partners/register/as-publisher is the
correct thing to do. Set it via CAREERJET_AFFID in .env.

`pagesize` is capped at 20 by the legacy endpoint regardless of what is
requested, and the `site` field always comes back empty.
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_API = "http://public.api.careerjet.net/search"

# Careerjet ignores pagesize above 20 on this endpoint.
_PAGE_SIZE = 20
_MAX_PAGES = 3
_TIMEOUT = 20
_PAGE_DELAY = 0.5

# Locale per country. Careerjet is locale-driven rather than country-code driven.
_LOCALES = {
    "DE": "de_DE", "AT": "de_AT", "CH": "de_CH",
    "GB": "en_GB", "US": "en_US", "NL": "nl_NL", "FR": "fr_FR", "ES": "es_ES",
}

_HEADERS = {
    # Required, and the value matters: Careerjet returns HTTP 401 for external
    # domains it doesn't recognise (a github.com Referer is rejected), but
    # accepts localhost. Verified by isolating Referer from affid — the
    # affiliate ID is not what's being validated here.
    "Referer": os.getenv("CAREERJET_REFERER", "https://localhost/jobfinder"),
    "User-Agent": "Mozilla/5.0 (compatible; JobFinder/1.0)",
}


def _affid() -> str:
    """
    Affiliate ID. Pinned deliberately — see the module docstring: changing this
    rewrites every result URL and would defeat URL-based deduplication.
    """
    return os.getenv("CAREERJET_AFFID", "6f9c1a2d8e4b7f3a5c0d9e8b7a6f5c4d")


def _parse_date(raw: str) -> str | None:
    """Careerjet returns RFC 2822, e.g. 'Sun, 02 Aug 2026 07:58:24 GMT'."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return None


def _clean(text: str) -> str:
    """Descriptions contain <b> highlight tags around matched terms."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def fetch(template: "JobSearchTemplate") -> list[Job]:
    """
    Search Careerjet for the template's keywords in `careerjet_location`
    (falling back to `location`).
    """
    location = (getattr(template, "careerjet_location", "") or template.location or "").strip()
    if not location:
        logger.info("Careerjet: no location configured — skipping")
        return []

    locale = _LOCALES.get((template.country_code or "DE").upper(), "de_DE")
    keywords = template.arbeitsagentur_queries or [template.indeed_position or "product manager"]

    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()

    for keyword in keywords:
        for page in range(1, _MAX_PAGES + 1):
            params = {
                "locale_code": locale,
                "keywords": keyword,
                "location": location,
                "sort": "date",           # newest first
                "pagesize": _PAGE_SIZE,
                "page": page,
                "affid": _affid(),
                "user_ip": "127.0.0.1",
                "user_agent": _HEADERS["User-Agent"],
            }
            try:
                r = session.get(_API, params=params, headers=_HEADERS, timeout=_TIMEOUT)
                r.raise_for_status()
                payload = r.json()
            except Exception as e:
                logger.warning("Careerjet '%s' page %d failed: %s", keyword, page, e)
                break

            if payload.get("type") != "JOBS":
                logger.debug("Careerjet '%s': non-JOBS response (%s)",
                             keyword, payload.get("type"))
                break

            items = payload.get("jobs") or []
            if not items:
                break

            for item in items:
                url = (item.get("url") or "").strip()
                title = (item.get("title") or "").strip()
                if not url or not title or url in seen:
                    continue

                description = _clean(item.get("description") or "")
                if not is_product_role(title) or is_excluded(title, description):
                    continue

                posted_date = _parse_date(item.get("date") or "")
                if not is_recent(posted_date, max_hours=30 * 24):
                    continue

                seen.add(url)
                jobs.append(Job(
                    title=title,
                    company=(item.get("company") or "").strip(),
                    location=(item.get("locations") or "").strip() or template.location,
                    url=url,
                    source="Careerjet",
                    seniority=classify_seniority(title, description),
                    posted_date=posted_date,
                    description=description,
                ))

            if len(items) < _PAGE_SIZE:
                break
            time.sleep(_PAGE_DELAY)

    logger.debug("Careerjet: %d jobs after filters", len(jobs))
    return jobs
