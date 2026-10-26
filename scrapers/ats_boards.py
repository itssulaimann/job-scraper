"""
ATS board scrapers — SmartRecruiters, Ashby, Personio, Lever, Workable.

All five are free, keyless, and queried per company slug. They complement the
Greenhouse scraper by covering the ATS platforms that German and Berlin/Munich
startups actually use.

Each `fetch_*` shares the same contract: take a template, read its slug list,
and return filtered Jobs. A slug that doesn't exist is skipped quietly — every
one of these APIs signals "no such company" differently:

    SmartRecruiters  HTTP 200 with totalFound: 0   (never 404)
    Ashby            HTTP 200 with an empty jobPostings array
    Personio         HTTP 200 with zero <position> elements
    Lever            HTTP 404
    Workable         HTTP 404 for unknown, 200 + empty results when not hiring

so each one validates on payload content rather than status code.

Only Personio and SmartRecruiters expose a posting date. Ashby and Lever
listings are current-openings-only, so a missing date is not treated as stale.
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_UA = {"User-Agent": "Mozilla/5.0 (compatible; JobFinder/1.0)"}

_SR_API      = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
_SR_POSTING  = "https://jobs.smartrecruiters.com/{company}/{job_id}"
_ASHBY_API   = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_PERSONIO_XML  = "https://{slug}.jobs.personio.de/xml"
_PERSONIO_POST = "https://{slug}.jobs.personio.de/job/{job_id}"
_LEVER_API   = "https://api.lever.co/v0/postings/{slug}?mode=json"
# Workable's public board API. The older widget endpoint
# (/api/v1/widget/accounts/{slug}) returns the account but an always-empty jobs
# array, so this v3 POST is the one that actually lists openings.
_WORKABLE_API  = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"
_WORKABLE_POST = "https://apply.workable.com/j/{shortcode}"

# Personio's own seniority vocabulary → this project's labels. Using the ATS's
# declared value is more reliable than inferring from the title.
_PERSONIO_SENIORITY = {
    "student": "Junior PM", "entry": "Junior PM", "junior": "Junior PM",
    "experienced": "Senior PM", "senior": "Senior PM",
    "lead": "Senior PM", "principal": "Senior PM",
    "manager": "Director", "director": "Director", "executive": "Director",
}


def _clean(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _iso_date(raw: str) -> str | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return raw[:10] if len(raw) >= 10 else None


def _location_matches(location: str, keywords: list[str]) -> bool:
    """ATS boards are global, so keep only offices in the template's region."""
    if not keywords:
        return True
    loc = (location or "").lower()
    return any(kw.lower() in loc for kw in keywords)


# ── SmartRecruiters ───────────────────────────────────────────────────────
def fetch_smartrecruiters(template: "JobSearchTemplate") -> list[Job]:
    companies = getattr(template, "smartrecruiters_companies", None) or []
    if not companies:
        return []

    country = (template.country_code or "DE").lower()
    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()

    for company in companies:
        try:
            r = session.get(_SR_API.format(company=company), headers=_UA, timeout=_TIMEOUT,
                            params={"country": country, "limit": 100})
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.debug("SmartRecruiters %s: %s", company, e)
            continue

        # An unknown slug returns 200 with totalFound: 0 rather than a 404.
        if not payload.get("totalFound"):
            logger.debug("SmartRecruiters %s: no postings", company)
            continue

        for item in payload.get("content") or []:
            job_id = str(item.get("id") or "")
            title = (item.get("name") or "").strip()
            if not job_id or not title:
                continue

            url = _SR_POSTING.format(company=company, job_id=job_id)
            if url in seen:
                continue

            loc = item.get("location") or {}
            location = (loc.get("fullLocation") or loc.get("city") or "").replace(", ,", ",").strip()
            if not _location_matches(location, template.location_keywords):
                continue

            if not is_product_role(title) or is_excluded(title, ""):
                continue

            posted_date = _iso_date(item.get("releasedDate") or "")
            if not is_recent(posted_date, max_hours=60 * 24):
                continue

            seen.add(url)
            jobs.append(Job(
                title=title,
                company=(item.get("company") or {}).get("name") or company,
                location=location or template.location,
                url=url,
                source="SmartRecruiters",
                seniority=classify_seniority(title, ""),
                posted_date=posted_date,
                description="",
            ))

    logger.debug("SmartRecruiters: %d jobs", len(jobs))
    return jobs


# ── Ashby ─────────────────────────────────────────────────────────────────
def fetch_ashby(template: "JobSearchTemplate") -> list[Job]:
    """
    Uses Ashby's public REST posting API rather than the GraphQL board query.

    The REST endpoint returns strictly more per posting — `descriptionPlain`,
    `publishedAt` and a canonical `jobUrl` — where the GraphQL board query
    exposes none of those. That means Ashby jobs get real descriptions for
    filtering and a real date for recency, instead of being date-blind.
    """
    slugs = getattr(template, "ashby_slugs", None) or []
    if not slugs:
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()

    for slug in slugs:
        try:
            r = session.get(_ASHBY_API.format(slug=slug), headers=_UA, timeout=_TIMEOUT)
            r.raise_for_status()
            postings = r.json().get("jobs") or []
        except Exception as e:
            logger.debug("Ashby %s: %s", slug, e)
            continue

        if not postings:
            logger.debug("Ashby %s: no postings (unknown slug?)", slug)
            continue

        for item in postings:
            title = (item.get("title") or "").strip()
            url = (item.get("jobUrl") or "").strip()
            if not title or not url or url in seen:
                continue

            # A role can be listed against several offices; keep it if any match.
            locations = [item.get("location") or ""]
            locations += [s.get("location") or ""
                          for s in (item.get("secondaryLocations") or [])]
            match = next(
                (l for l in locations if _location_matches(l, template.location_keywords)),
                None,
            )
            if match is None:
                continue

            description = _clean(item.get("descriptionPlain") or "")
            if not is_product_role(title) or is_excluded(title, description):
                continue

            posted_date = _iso_date(item.get("publishedAt") or "")
            if not is_recent(posted_date, max_hours=90 * 24):
                continue

            seen.add(url)
            jobs.append(Job(
                title=title,
                company=slug.replace("-", " ").title(),
                location=match.strip() or template.location,
                url=url,
                source="Ashby",
                seniority=classify_seniority(title, description),
                posted_date=posted_date,
                description=description,
            ))

    logger.debug("Ashby: %d jobs", len(jobs))
    return jobs


# ── Personio ──────────────────────────────────────────────────────────────
def fetch_personio(template: "JobSearchTemplate") -> list[Job]:
    slugs = getattr(template, "personio_slugs", None) or []
    if not slugs:
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()

    for slug in slugs:
        try:
            r = session.get(_PERSONIO_XML.format(slug=slug), headers=_UA, timeout=_TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            logger.debug("Personio %s: %s", slug, e)
            continue

        positions = root.findall(".//position")
        if not positions:
            logger.debug("Personio %s: no positions (unknown slug?)", slug)
            continue

        for pos in positions:
            def field(tag: str) -> str:
                el = pos.find(tag)
                return (el.text or "").strip() if el is not None and el.text else ""

            job_id = field("id")
            title = field("name")
            if not job_id or not title:
                continue

            url = _PERSONIO_POST.format(slug=slug, job_id=job_id)
            if url in seen:
                continue

            office = field("office")
            if not _location_matches(office, template.location_keywords):
                continue

            # jobDescriptions holds nested <jobDescription> children.
            description = _clean(" ".join(
                (el.text or "") for el in pos.iter() if el.tag == "value"
            ))

            if not is_product_role(title) or is_excluded(title, description):
                continue

            posted_date = _iso_date(field("createdAt"))
            if not is_recent(posted_date, max_hours=90 * 24):
                continue

            # Prefer Personio's declared seniority over title inference.
            seniority = _PERSONIO_SENIORITY.get(field("seniority").lower()) \
                or classify_seniority(title, description)

            seen.add(url)
            jobs.append(Job(
                title=title,
                company=slug.replace("-", " ").title(),
                location=office or template.location,
                url=url,
                source="Personio",
                seniority=seniority,
                posted_date=posted_date,
                description=description,
            ))

    logger.debug("Personio: %d jobs", len(jobs))
    return jobs


# ── Lever ─────────────────────────────────────────────────────────────────
def fetch_lever(template: "JobSearchTemplate") -> list[Job]:
    slugs = getattr(template, "lever_slugs", None) or []
    if not slugs:
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()

    for slug in slugs:
        try:
            r = session.get(_LEVER_API.format(slug=slug), headers=_UA, timeout=_TIMEOUT)
            if r.status_code == 404:
                logger.debug("Lever %s: unknown slug", slug)
                continue
            r.raise_for_status()
            items = r.json()
        except Exception as e:
            logger.debug("Lever %s: %s", slug, e)
            continue

        for item in items or []:
            url = (item.get("hostedUrl") or "").strip()
            title = (item.get("text") or "").strip()
            if not url or not title or url in seen:
                continue

            location = ((item.get("categories") or {}).get("location") or "").strip()
            if not _location_matches(location, template.location_keywords):
                continue

            description = _clean(item.get("descriptionPlain") or item.get("description") or "")
            if not is_product_role(title) or is_excluded(title, description):
                continue

            created = item.get("createdAt")
            posted_date = None
            if isinstance(created, (int, float)) and created > 0:
                try:
                    posted_date = datetime.fromtimestamp(created / 1000).date().isoformat()
                except (ValueError, OverflowError, OSError):
                    posted_date = None

            seen.add(url)
            jobs.append(Job(
                title=title,
                company=slug.replace("-", " ").title(),
                location=location or template.location,
                url=url,
                source="Lever",
                seniority=classify_seniority(title, description),
                posted_date=posted_date,
                description=description,
            ))

    logger.debug("Lever: %d jobs", len(jobs))
    return jobs


# ── Workable ──────────────────────────────────────────────────────────────
def fetch_workable(template: "JobSearchTemplate") -> list[Job]:
    slugs = getattr(template, "workable_slugs", None) or []
    if not slugs:
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    session = requests.Session()
    headers = {**_UA, "Content-Type": "application/json", "Accept": "application/json"}

    for slug in slugs:
        try:
            r = session.post(_WORKABLE_API.format(slug=slug), headers=headers,
                             json={"query": ""}, timeout=_TIMEOUT)
            if r.status_code == 404:
                logger.debug("Workable %s: unknown slug", slug)
                continue
            r.raise_for_status()
            items = r.json().get("results") or []
        except Exception as e:
            logger.debug("Workable %s: %s", slug, e)
            continue

        if not items:
            logger.debug("Workable %s: no openings", slug)
            continue

        for item in items:
            title = (item.get("title") or "").strip()
            shortcode = (item.get("shortcode") or "").strip()
            if not title or not shortcode:
                continue

            url = _WORKABLE_POST.format(shortcode=shortcode)
            if url in seen:
                continue

            # `location` is a single office; `locations` lists every office the
            # role is open in. Keep the role if any of them matches.
            candidates = list(item.get("locations") or [])
            if item.get("location"):
                candidates.append(item["location"])
            location = ""
            for loc in candidates:
                text = loc.get("display") or ", ".join(
                    p for p in (loc.get("city"), loc.get("country")) if p
                )
                if _location_matches(text, template.location_keywords):
                    location = text
                    break
            if not location:
                continue

            if not is_product_role(title) or is_excluded(title, ""):
                continue

            posted_date = _iso_date(item.get("published") or "")
            if not is_recent(posted_date, max_hours=90 * 24):
                continue

            seen.add(url)
            jobs.append(Job(
                title=title,
                company=slug.replace("-", " ").title(),
                location=location or template.location,
                url=url,
                source="Workable",
                seniority=classify_seniority(title, ""),
                posted_date=posted_date,
                description="",
            ))

    logger.debug("Workable: %d jobs", len(jobs))
    return jobs


def fetch(template: "JobSearchTemplate") -> list[Job]:
    """Run every configured ATS board and return the combined result."""
    jobs: list[Job] = []
    for name, fn in (
        ("SmartRecruiters", fetch_smartrecruiters),
        ("Ashby",           fetch_ashby),
        ("Personio",        fetch_personio),
        ("Lever",           fetch_lever),
        ("Workable",        fetch_workable),
    ):
        try:
            found = fn(template)
            if found:
                logger.info("    %s → %d jobs", name, len(found))
            jobs.extend(found)
        except Exception as e:
            logger.warning("    %s failed: %s", name, e)
    return jobs
