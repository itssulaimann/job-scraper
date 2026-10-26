"""
RSS feed scrapers — free, no key, no Apify.

Discovered via ever-jobs/ever-jobs (TypeScript reference implementation).
Both feeds are plain RSS XML, parseable with stdlib xml.etree.ElementTree.

Sources:
  GermanTechJobs  — https://germantechjobs.de/rss
    All German tech jobs; titles include salary ranges "@ Company [min - max EUR]".
    Full feed is ~13 MB (~2,400 items); we filter in-stream for PM roles.

  BerlinStartupJobs — https://berlinstartupjobs.com/wp-json/wp/v2/posts
    Berlin-specific board. Uses the WordPress REST API rather than the RSS
    feed, which is capped at ~12 items — the API exposes the full board
    (127 live listings when this was written).
    Title format: "Job Title // Company"
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_HEADERS = {
    "Accept":     "application/rss+xml, application/xml, text/xml",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


# ── Helpers ───────────────────────────────────────────────────────────────
def _parse_pubdate(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _html_text(text: str) -> str:
    """Strip tags, unescape entities, and collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(_strip_html(text))).strip()


def _salary_from_title(title: str) -> tuple[int | None, int | None]:
    """Extract (min, max) from '[40.000 - 70.000 EUR]' suffix if present."""
    m = re.search(r"\[\s*([\d.,]+)\s*-\s*([\d.,]+)\s*EUR\s*\]", title)
    if not m:
        return None, None
    def parse(s: str) -> int:
        return int(s.replace(".", "").replace(",", ""))
    try:
        return parse(m.group(1)), parse(m.group(2))
    except ValueError:
        return None, None


# ── GermanTechJobs ────────────────────────────────────────────────────────
_GTJ_URL = "https://germantechjobs.de/rss"

def _fetch_germantechjobs(template: "JobSearchTemplate") -> list[Job]:
    """
    Parse GermanTechJobs RSS. The full feed is ~13 MB — we stream it and
    stop early once we've seen enough recent items to avoid loading it all.
    """
    try:
        r = requests.get(_GTJ_URL, headers=_HEADERS, timeout=_TIMEOUT, stream=True)
        r.raise_for_status()
        # Read in chunks; 13 MB is manageable but we cap at 20 MB as a guard
        chunks = []
        for chunk in r.iter_content(chunk_size=256 * 1024):
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > 20 * 1024 * 1024:
                break
        xml_bytes = b"".join(chunks)
    except Exception as e:
        logger.warning("GermanTechJobs: fetch failed — %s", e)
        return []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.warning("GermanTechJobs: XML parse error — %s", e)
        return []

    items = root.findall(".//item")
    logger.debug("GermanTechJobs: %d items in feed", len(items))

    cutoff = (date.today() - timedelta(days=30)).isoformat()
    jobs: list[Job] = []
    seen: set[str] = set()

    for item in items:
        raw_title = (item.findtext("title") or "").strip()
        url       = (item.findtext("link") or "").strip()
        pub_date  = _parse_pubdate(item.findtext("pubDate"))

        if not url or url in seen:
            continue
        if pub_date and pub_date < cutoff:
            continue

        # Title format: "Job Title @ Company [min - max EUR]" — strip salary suffix
        title = re.sub(r"\s*\[[\d.,\s\-]+EUR\]\s*$", "", raw_title).strip()
        # Strip company part after " @ "
        title_only = re.sub(r"\s*@\s*.+$", "", title).strip() or title

        desc_html = item.findtext("description") or ""
        desc      = _strip_html(desc_html)

        if not is_product_role(title_only) or is_excluded(title_only, desc):
            continue

        # Extract company: "Job @ Company [salary]" → Company
        company_match = re.search(r"@\s*(.+?)(?:\s*\[|$)", raw_title)
        company = company_match.group(1).strip() if company_match else ""

        seen.add(url)
        jobs.append(Job(
            title=title_only,
            company=company,
            location="Germany",          # feed doesn't have per-job location
            url=url,
            source="GermanTechJobs",
            seniority=classify_seniority(title_only, desc),
            posted_date=pub_date,
            description=desc,
        ))

    logger.debug("GermanTechJobs: %d PM jobs after filters", len(jobs))
    return jobs


# ── BerlinStartupJobs ─────────────────────────────────────────────────────
# The public RSS feed is capped at ~12 items. The same WordPress install
# exposes its full post list over the REST API — 127 live listings at time of
# writing, i.e. ~10x the coverage from the same source.
#
# `job_company` comes back as taxonomy term IDs rather than names. Most titles
# carry the company after a "//" separator, but not all — so any job that
# survives filtering without a parsed company has its term ID resolved in a
# single batched lookup (?include=id1,id2,...). The full taxonomy is ~4,800
# terms, far too many to fetch wholesale, but resolving only the handful of
# matches costs exactly one extra request.
_BSJ_API  = "https://berlinstartupjobs.com/wp-json/wp/v2/posts"
_BSJ_TERMS = "https://berlinstartupjobs.com/wp-json/wp/v2/job_company"
_BSJ_HDRS = {
    "Accept":     "application/json",
    "User-Agent": "ever-jobs/0.1.0 (job-aggregator)",  # exact UA the site allows
}
_BSJ_PER_PAGE = 100
_BSJ_MAX_PAGES = 3

def _fetch_berlinstartupjobs(template: "JobSearchTemplate") -> list[Job]:
    """
    Fetch BerlinStartupJobs via its WordPress REST API.
    Title format: "Job Title // Company"
    """
    jobs: list[Job] = []
    seen: set[str] = set()
    pending_company: dict[int, list[Job]] = {}   # term id → jobs awaiting a name

    for page in range(1, _BSJ_MAX_PAGES + 1):
        try:
            r = requests.get(
                _BSJ_API, headers=_BSJ_HDRS, timeout=_TIMEOUT, allow_redirects=True,
                params={
                    "per_page": _BSJ_PER_PAGE,
                    "page": page,
                    "_fields": "id,date,link,title,excerpt,job_company",
                },
            )
            # WordPress 400s on a page beyond the last one.
            if r.status_code == 400:
                break
            r.raise_for_status()
            items = r.json()
        except Exception as e:
            logger.warning("BerlinStartupJobs: fetch failed — %s", e)
            break

        if not items:
            break

        logger.debug("BerlinStartupJobs: %d items on page %d", len(items), page)

        for item in items:
            url = (item.get("link") or "").strip()
            if not url or url in seen:
                continue

            raw_title = _html_text((item.get("title") or {}).get("rendered", ""))
            if not raw_title:
                continue

            desc = _html_text((item.get("excerpt") or {}).get("rendered", ""))

            # "Job Title // Company"
            parts   = raw_title.split("//")
            title   = parts[0].strip()
            company = parts[-1].strip() if len(parts) > 1 else ""

            if not is_product_role(title) or is_excluded(title, desc):
                continue

            seen.add(url)
            job = Job(
                title=title,
                company=company,
                location="Berlin, Germany",
                url=url,
                source="BerlinStartupJobs",
                seniority=classify_seniority(title, desc),
                posted_date=(item.get("date") or "")[:10] or None,
                description=desc,
            )
            jobs.append(job)

            if not company:
                term_ids = item.get("job_company") or []
                if term_ids:
                    pending_company.setdefault(int(term_ids[0]), []).append(job)

        if len(items) < _BSJ_PER_PAGE:
            break

    if pending_company:
        _resolve_bsj_companies(pending_company)

    logger.debug("BerlinStartupJobs: %d PM jobs after filters", len(jobs))
    return jobs


def _resolve_bsj_companies(pending: dict[int, list[Job]]) -> None:
    """Fill in company names for jobs whose title lacked a '// Company' suffix."""
    try:
        r = requests.get(
            _BSJ_TERMS, headers=_BSJ_HDRS, timeout=_TIMEOUT, allow_redirects=True,
            params={"include": ",".join(str(i) for i in pending),
                    "per_page": 100, "_fields": "id,name"},
        )
        r.raise_for_status()
        terms = r.json()
    except Exception as e:
        logger.debug("BerlinStartupJobs: company lookup failed — %s", e)
        return

    for term in terms:
        name = _html_text(term.get("name") or "")
        for job in pending.get(term.get("id"), []):
            job.company = name


# ── Public entry point ────────────────────────────────────────────────────
def fetch(template: "JobSearchTemplate") -> list[Job]:
    """Fetch from all RSS sources and return combined PM job list."""
    jobs: list[Job] = []
    jobs.extend(_fetch_germantechjobs(template))
    jobs.extend(_fetch_berlinstartupjobs(template))
    return jobs
