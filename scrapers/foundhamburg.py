"""
found Hamburg job board — free, no key, no Apify.

https://foundhamburg.com/jobs/ — Hamburg startup/scale-up job board (SaaS, AI,
Adtech, Climate, E-commerce...). Fully server-rendered WordPress/Bricks page:
every listing (289 at time of writing) sits in the initial HTML as an
`<a data-job-card="true">` card with title, company, role/industry tags, a
DD.MM.YYYY post date, and a direct external application URL already in its
attributes — no pagination, no JS, no API to reverse-engineer.

The board is Hamburg-specific by definition, so location is hardcoded rather
than parsed per listing (there is no per-job location field to read).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import requests
from bs4 import BeautifulSoup

from scrapers.base import Job
from classifier import is_excluded, is_product_role, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_URL = "https://foundhamburg.com/jobs/"
_TIMEOUT = 20
_HEADERS = {
    "Accept":     "text/html",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
_MAX_AGE_DAYS = 30


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def fetch(template: "JobSearchTemplate") -> list[Job]:
    try:
        r = requests.get(_URL, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.warning("FoundHamburg: fetch failed — %s", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select('a[data-job-card="true"]')
    logger.debug("FoundHamburg: %d cards on page", len(cards))

    cutoff = (date.today() - timedelta(days=_MAX_AGE_DAYS)).isoformat()
    jobs: list[Job] = []
    seen: set[str] = set()

    for card in cards:
        url = (card.get("href") or "").strip()
        if not url or url in seen:
            continue

        title_el = card.select_one(".job-titel")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        posted_date = _parse_date(
            (card.select_one(".job-date").get_text(strip=True) if card.select_one(".job-date") else None)
        )
        if posted_date and posted_date < cutoff:
            continue

        desc_el = card.select_one(".job-description")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        if not is_product_role(title) or is_excluded(title, desc):
            continue

        company = (card.get("data-job-company") or "").strip()

        seen.add(url)
        jobs.append(Job(
            title=title,
            company=company,
            location="Hamburg, Germany",
            url=url,
            source="FoundHamburg",
            seniority=classify_seniority(title, desc),
            posted_date=posted_date,
            description=desc,
        ))

    logger.debug("FoundHamburg: %d PM jobs after filters", len(jobs))
    return jobs
