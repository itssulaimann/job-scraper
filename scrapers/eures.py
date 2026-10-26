"""
EURES scraper — the European Commission's public job mobility network.

Free, keyless, no registration. One POST endpoint returns the whole EU job
market; we narrow it by NUTS region and keyword.

    POST https://europa.eu/eures/api/jv-searchengine/public/jv-search/search

Scale (measured Aug 2026): 8,753 vacancies for "product manager" in Berlin
(de3) and 35,926 in Bayern (de2).

Overlap caveat: EURES ingests most of its German volume from the Bundesagentur
für Arbeit, which `arbeitsagentur.py` already scrapes directly — measured at
~45% exact title+employer overlap. The other ~55% is genuinely new, much of it
"Product Owner" phrasing that a "Product Manager" query alone misses. The
two-layer dedup in notion_writer.py absorbs the rest.

Region codes are lowercase NUTS-1 and only NUTS-1 is accepted — `de300` and
`de21` are rejected with 0 results. To target a city inside a large region
(Munich within Bayern) query the NUTS-1 code and narrow client-side with
`eures_nuts_prefixes`.

Keyword matching with specificSearchCode=EVERYWHERE scans full job text, so
`numberRecords` is heavily inflated relative to real title matches. We sort by
relevance and let the template's own title filter do the real work.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_API = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
_DETAIL = "https://europa.eu/eures/portal/jv-se/jv-details/{jv_id}"

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; JobFinder/1.0)",
}

_PER_PAGE = 50
_MAX_PAGES = 4          # 200 relevance-ranked results per keyword is plenty
_TIMEOUT = 30

# EURES resets the connection when queried back-to-back, so pace the requests
# and retry a reset once before giving up on the page.
_PAGE_DELAY = 1.0
_PAGE_ATTEMPTS = 2


def _body(keyword: str, location_codes: list[str], page: int) -> dict:
    """
    Build the search payload.

    The `keywords` shape matters: it is a list of
    {"keyword": ..., "specificSearchCode": ...} objects. The keywordType/values
    form documented in some places is rejected.
    """
    return {
        "resultsPerPage": _PER_PAGE,
        "page": page,
        "sortSearch": "BEST_MATCH",
        "keywords": [{"keyword": keyword, "specificSearchCode": "EVERYWHERE"}],
        "publicationPeriod": None,
        "occupationUris": [],
        "skillUris": [],
        "requiredExperienceCodes": [],
        "positionScheduleCodes": [],
        "sectorCodes": [],
        "educationAndQualificationLevelCodes": [],
        "positionOfferingCodes": [],
        "locationCodes": location_codes,
        "euresFlagCodes": [],
        "otherBenefitsCodes": [],
        "requiredLanguages": [],
        "minNumberPost": None,
        "sessionId": "jobfinder",
        "userPreferredLanguage": None,
        "requestLanguage": "en",
    }


def _parse_date(epoch_ms) -> str | None:
    if not isinstance(epoch_ms, (int, float)) or epoch_ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _clean(text: str) -> str:
    """EURES descriptions carry <br> tags and markdown-ish ** emphasis."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("**", "")
    return re.sub(r"\s+", " ", text).strip()


def _nuts_codes(location_map: dict | None) -> list[str]:
    out: list[str] = []
    for codes in (location_map or {}).values():
        out.extend(c for c in (codes or []) if c)
    return out


def _matches_region(nuts: list[str], prefixes: list[str]) -> bool:
    """No prefixes configured means the whole queried region counts."""
    if not prefixes:
        return True
    return any(c.upper().startswith(p.upper()) for c in nuts for p in prefixes)


def fetch(template: "JobSearchTemplate") -> list[Job]:
    """
    Query EURES for each of the template's Arbeitsagentur keywords, restricted
    to `eures_location_codes` and optionally narrowed by `eures_nuts_prefixes`.
    """
    location_codes = [c.lower() for c in (getattr(template, "eures_location_codes", None) or [])]
    if not location_codes:
        logger.info("EURES: no eures_location_codes configured — skipping")
        return []

    prefixes = getattr(template, "eures_nuts_prefixes", None) or []
    keywords = template.arbeitsagentur_queries or ["Product Manager"]

    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()

    for keyword in keywords:
        for page in range(1, _MAX_PAGES + 1):
            payload = None
            for attempt in range(1, _PAGE_ATTEMPTS + 1):
                try:
                    r = session.post(_API, headers=_HEADERS, timeout=_TIMEOUT,
                                     json=_body(keyword, location_codes, page))
                    r.raise_for_status()
                    payload = r.json()
                    break
                except Exception as e:
                    if attempt == _PAGE_ATTEMPTS:
                        logger.warning("EURES '%s' page %d failed: %s", keyword, page, e)
                    else:
                        time.sleep(2.0)
            if payload is None:
                break

            items = payload.get("jvs") or []
            if not items:
                break

            for item in items:
                jv_id = item.get("id")
                if not jv_id:
                    continue
                url = _DETAIL.format(jv_id=jv_id)
                if url in seen:
                    continue

                title = (item.get("title") or "").strip()
                if not title:
                    continue

                if not _matches_region(_nuts_codes(item.get("locationMap")), prefixes):
                    continue

                description = _clean(item.get("description") or "")
                if not is_product_role(title) or is_excluded(title, description):
                    continue

                posted_date = _parse_date(item.get("creationDate"))
                if not is_recent(posted_date, max_hours=30 * 24):
                    continue

                seen.add(url)
                jobs.append(Job(
                    title=title,
                    company=((item.get("employer") or {}).get("name") or "").strip(),
                    location=template.location,
                    url=url,
                    source="EURES",
                    seniority=classify_seniority(title, description),
                    posted_date=posted_date,
                    description=description,
                ))

            if len(items) < _PER_PAGE:
                break
            time.sleep(_PAGE_DELAY)

    logger.debug("EURES: %d jobs after filters", len(jobs))
    return jobs
