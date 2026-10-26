"""Arbeitsagentur (Bundesagentur für Arbeit) scraper — free public REST API."""
from __future__ import annotations

from typing import TYPE_CHECKING

import requests

from scrapers.base import Job
from classifier import is_excluded, is_product_role, is_recent, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
_HEADERS = {
    "X-API-Key": "jobboerse-jobsuche",
    "User-Agent": "Jobsuche/2.9.2 (de.arbeitsagentur.jobboerse; build:1077; iOS 15.1.0) Alamofire/5.4.4",
}


def _fetch_query(query: str, location: str, seen: set[str]) -> list[Job]:
    jobs: list[Job] = []
    page = 1

    while True:
        try:
            resp = requests.get(
                _BASE_URL,
                headers=_HEADERS,
                params={"was": query, "wo": location, "umkreis": 25,
                        "angebotsart": 1, "page": page, "size": 100},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException:
            break

        data = resp.json()
        items = data.get("stellenangebote") or []
        if not items:
            break

        for item in items:
            refnr = item.get("refnr", "")
            if not refnr:
                continue
            url = f"https://www.arbeitsagentur.de/jobsuche/suche?angNr={refnr}"

            if url in seen:
                continue
            seen.add(url)

            title = item.get("titel", "")
            if not title or not is_product_role(title) or is_excluded(title, ""):
                continue

            posted_raw = item.get("aktuelleVeroeffentlichungsdatum", "") or ""
            posted_date = posted_raw[:10] if posted_raw else None
            if not is_recent(posted_date):
                continue

            arbeitsort = item.get("arbeitsort") or {}
            loc = arbeitsort.get("ort", location) if isinstance(arbeitsort, dict) else location
            company = item.get("arbeitgeber", "")

            jobs.append(Job(
                title=title,
                company=company,
                location=loc,
                url=url,
                source="Arbeitsagentur",
                seniority=classify_seniority(title, ""),
                posted_date=posted_date,
                description="",
            ))

        total = data.get("maxErgebnisse", 0)
        if page * 100 >= total or page >= 10:
            break
        page += 1

    return jobs


def fetch(template: JobSearchTemplate) -> list[Job]:
    seen: set[str] = set()
    all_jobs: list[Job] = []
    for query in template.arbeitsagentur_queries:
        all_jobs.extend(_fetch_query(query, template.location, seen))
    return all_jobs
