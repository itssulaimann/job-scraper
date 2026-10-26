"""Speedrun Talent Network scraper — free open API (a16z Speedrun portfolio board).

Docs: https://speedrun-talent-network.com/developers
No auth. Rate-limited at the edge; identifying via ?source= improves priority.
`loc` values are case-sensitive ("Berlin" matches, "berlin" does not).
Postings are long-lived, so the recency window is 60 days rather than the
48h default — the URL-hash dedup layer prevents repeat Notion writes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

_API = "https://speedrun-talent-network.com/api/v1/jobs"
_AGENT = "jobfinder"
_MAX_PAGES_PER_LOC = 5
_MAX_DETAIL_FETCHES = 25
_RECENT_HOURS = 24 * 60  # 60 days


def _locations(template: JobSearchTemplate) -> list[str]:
    city = template.location.split(",")[0].strip().title()
    locs = [city, "Germany"]  # "Germany" catches remote-DE postings
    return list(dict.fromkeys(locs))


def _fetch_description(session: requests.Session, job_id: str) -> str:
    try:
        resp = session.get(f"{_API}/{job_id}", params={"source": _AGENT}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        job = data.get("job") or data
        return str(job.get("description_text") or job.get("description") or "")
    except Exception:
        return ""


def fetch(template: JobSearchTemplate) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_fetches = 0
    session = requests.Session()

    for loc in _locations(template):
        page = 0
        while page < _MAX_PAGES_PER_LOC:
            resp = session.get(
                _API,
                params={"loc": loc, "page": page, "source": _AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("jobs", [])
            if not items:
                break

            for item in items:
                job_id = item.get("id", "")
                url: str = item.get("url", "")
                if not url or not job_id or job_id in seen:
                    continue
                seen.add(job_id)

                title: str = (item.get("title") or "").strip()
                posted_date = (item.get("published_at") or "")[:10] or None

                if not is_product_role(title) or is_excluded(title):
                    continue
                if not is_recent(posted_date, max_hours=_RECENT_HOURS):
                    continue

                description = ""
                if detail_fetches < _MAX_DETAIL_FETCHES:
                    description = _fetch_description(session, job_id)
                    detail_fetches += 1
                if is_excluded(title, description):
                    continue

                jobs.append(Job(
                    title=title,
                    company=item.get("company", ""),
                    location=item.get("location") or template.location,
                    url=url,
                    source="Speedrun",
                    seniority=classify_seniority(title, description),
                    posted_date=posted_date,
                    description=description[:500],
                ))

            page += 1
            if page >= int(data.get("total_pages") or 0):
                break

    return jobs
