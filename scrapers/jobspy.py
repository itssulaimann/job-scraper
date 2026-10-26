"""
JobSpy scraper — free LinkedIn + Indeed scraping, no API keys required.

Uses the python-jobspy library which calls LinkedIn's public guest API directly:
  https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search

LinkedIn allows ~490 results per search query from this endpoint with no
authentication. Running separate searches per keyword (e.g. "product manager"
then "product owner") each get their own 490-result budget, so coverage scales
with the number of keywords in the template.

Replaces / supplements the Apify-based linkedin.py and indeed.py scrapers when
the monthly Apify quota is exhausted.

Limitations:
  - LinkedIn may return fewer results after repeated rapid calls from the same IP
  - Indeed occasionally blocks with a CAPTCHA (returns 0 results silently)
  - Neither restriction applies to the Apify actors, so this is a fallback

Two JobSpy behaviours are worked around here:

  1. Silent truncation — JobSpy bails out of a search on the first empty page
     or first 429 with no retry. Empty pages are transient (the same query can
     return 10, 10, then 0 on identical retries), so one unlucky page would
     otherwise end a whole keyword's scrape early. `_scrape_with_retry` retries.

  2. Empty LinkedIn descriptions — JobSpy only populates `description` when
     `linkedin_fetch_description=True`, and even then it fetches the now
     auth-walled `/jobs/view/{id}`. Descriptions therefore arrive empty, so
     `is_excluded()` and `classify_seniority()` were running on "" for every
     LinkedIn job — which is why nearly everything landed on `seniority_default`.
     We enrich via the guest endpoint instead (see scrapers/linkedin_guest.py).

Install: pip install python-jobspy
"""
from __future__ import annotations

import logging
import time
import warnings
from typing import TYPE_CHECKING

from scrapers.base import Job
from scrapers import linkedin_guest
from classifier import is_excluded, is_product_role, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

_DEFAULT_SITES = ["linkedin", "indeed"]

# Retry policy for transient empty pages / rate limiting.
_MAX_ATTEMPTS   = 3
_RETRY_BACKOFF  = [5, 15]   # seconds before attempt 2 and 3

# Cap on LinkedIn detail fetches per run. Each costs ~3s, so 60 ≈ 3 minutes.
# Only jobs that already passed the title filter are enriched, so this is spent
# on plausible matches rather than on rows we are about to discard.
_ENRICH_CAP = 60


def _safe_str(val) -> str:
    """Convert a pandas/numpy value to str, returning '' for NaN/None."""
    if val is None:
        return ""
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return ""
    except Exception:
        pass
    return str(val).strip()


def _scrape_with_retry(scrape_jobs, **kwargs):
    """
    Call JobSpy, retrying when it returns nothing.

    JobSpy treats an empty page or a 429 as end-of-results and returns silently.
    Both are frequently transient, so an empty frame is retried with backoff
    rather than accepted as "no jobs".
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = scrape_jobs(**kwargs)
        except Exception as e:
            if attempt == _MAX_ATTEMPTS:
                logger.warning("    JobSpy failed after %d attempts: %s", attempt, e)
                return None
            wait = _RETRY_BACKOFF[attempt - 1]
            logger.info("    attempt %d failed (%s) — retrying in %ds", attempt, e, wait)
            time.sleep(wait)
            continue

        if df is not None and not df.empty:
            return df

        if attempt < _MAX_ATTEMPTS:
            wait = _RETRY_BACKOFF[attempt - 1]
            logger.info("    attempt %d returned 0 rows — retrying in %ds", attempt, wait)
            time.sleep(wait)

    return None


def fetch(template: "JobSearchTemplate") -> list[Job]:
    """
    Scrape LinkedIn and Indeed for the template's search terms and location.

    LinkedIn: runs one search per keyword in template.linkedin_keywords —
    each search gets its own ~490-result budget, maximising total coverage.

    Indeed: runs a single combined OR search (fewer results but no per-keyword
    benefit on Indeed's engine).

    Filtering happens in two passes so that no network request is wasted:
      pass 1 — cheap title-only filter on every raw row
      pass 2 — enrich surviving LinkedIn rows with real descriptions, then
               apply the description-dependent exclusion and seniority rules
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.error("JobSpy not installed. Run: pip install python-jobspy")
        return []

    search_terms = template.linkedin_keywords if template.linkedin_keywords \
                   else ["product manager"]

    location      = f"{template.location}, Germany"
    hours_old     = 72    # jobs posted in the last 3 days
    results_n     = 200   # LinkedIn allows ~490 per search; 200 is a safe ceiling

    _country_map = {
        "DE": "germany", "AT": "austria", "CH": "switzerland",
        "GB": "uk", "US": "usa", "FR": "france", "NL": "netherlands",
    }
    country_name = _country_map.get(template.country_code.upper(),
                                    template.country_code.lower())

    # Indeed: combine all terms into one OR search
    indeed_term = " OR ".join(search_terms)

    # ── Pass 1: collect raw rows, filter on title only (no network cost) ──
    candidates: list[dict] = []
    seen: set[str] = set()

    for site in _DEFAULT_SITES:
        # LinkedIn: separate search per keyword for maximum coverage
        terms = search_terms if site == "linkedin" else [indeed_term]

        for search_term in terms:
            logger.info("  JobSpy → %s | '%s' | %s | last %dh",
                        site, search_term, location, hours_old)

            df = _scrape_with_retry(
                scrape_jobs,
                site_name=[site],
                search_term=search_term,
                location=location,
                results_wanted=results_n,
                hours_old=hours_old,
                country_indeed=country_name,
                verbose=0,
            )

            if df is None or df.empty:
                logger.info("    → 0 results")
                continue

            logger.info("    → %d raw results", len(df))

            for _, row in df.iterrows():
                url = _safe_str(row.get("job_url"))
                if not url or url in seen:
                    continue

                title = _safe_str(row.get("title"))
                if not title or not is_product_role(title):
                    continue

                raw_date = row.get("date_posted")
                try:
                    posted_date = raw_date.isoformat() if hasattr(raw_date, "isoformat") else None
                except Exception:
                    posted_date = None

                seen.add(url)
                candidates.append({
                    "url":         url,
                    "title":       title,
                    "company":     _safe_str(row.get("company")),
                    "location":    _safe_str(row.get("location")) or template.location,
                    "description": _safe_str(row.get("description")),
                    "posted_date": posted_date,
                    "site":        site,
                })

    # ── Pass 2: enrich LinkedIn rows that arrived without a description ──
    needs_desc = [c["url"] for c in candidates
                  if c["site"] == "linkedin" and not c["description"]]
    if needs_desc:
        logger.info("    LinkedIn enrich: %d candidates need descriptions", len(needs_desc))
        enriched = linkedin_guest.enrich_many(needs_desc, max_jobs=_ENRICH_CAP)
        for c in candidates:
            detail = enriched.get(c["url"])
            if not detail:
                continue
            c["description"] = detail["description"]
            # LinkedIn's own "Seniority level" is a useful hint, but the
            # template's rules stay authoritative — see below.
            c["li_seniority"] = detail["criteria"].get("Seniority level", "")

    # ── Pass 3: description-dependent filters ──
    jobs: list[Job] = []
    for c in candidates:
        desc = c["description"]
        if is_excluded(c["title"], desc):
            continue

        jobs.append(Job(
            title=c["title"],
            company=c["company"],
            location=c["location"],
            url=c["url"],
            source=c["site"].capitalize(),   # "Linkedin" / "Indeed"
            seniority=classify_seniority(c["title"], desc),
            posted_date=c["posted_date"],
            description=desc,
        ))

    logger.info("  JobSpy total: %d jobs after filters", len(jobs))
    return jobs
