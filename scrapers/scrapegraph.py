"""
ScrapeGraphAI scraper — AI-powered extraction from company career pages.

Scrapes job listings from any URL (company careers pages, niche boards, etc.)
without needing Apify. Uses a three-tier approach:

  Tier 1 — BeautifulSoup (fast, zero tokens):
    If the page renders job listings in plain HTML with recognisable department
    headings (Greenhouse, and similar boards), extract directly — no LLM needed.

  Tier 2 — Scrapling Fetcher (stealth HTTP, zero tokens):
    Re-fetches with a Chrome TLS fingerprint + randomised headers and re-runs the
    BS4 extractor on the richer HTML.  Catches pages that return a bot-detection
    wall to plain requests but serve real content to a browser-like UA.

  Tier 3 — ScrapeGraphAI LLM (flexible, token-based):
    For pages that don't have a parsable structure (custom career pages, Webflow
    sites, etc.), fall back to the LLM which interprets the content semantically.

Supported LLM backends (set ONE of these in .env):
  GROQ_API_KEY=gsk_...          -> llama-3.1-8b-instant (free tier)
  SGAI_API_KEY=sgai_...         -> ScrapeGraph cloud (100 free credits)
  OPENAI_API_KEY=sk-...         -> gpt-4o-mini
  ANTHROPIC_API_KEY=sk-ant-...  -> claude-3-haiku-20240307
  GEMINI_API_KEY=...            -> gemini-1.5-flash
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from scrapers.base import Job
from classifier import is_excluded, is_product_role, classify_seniority

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

logger = logging.getLogger(__name__)

# Headings that identify a product/PM department section in HTML job boards
_PM_SECTION_KEYWORDS = {
    "product management", "product & design", "product & engineering",
    "product & ux", "product",
}


# ── Pydantic schema ───────────────────────────────────────────────────────
class _JobItem(BaseModel):
    title: str                 = Field(description="Job title")
    company: str               = Field(description="Company name")
    location: Optional[str]    = Field(None, description="Job location")
    url: Optional[str]         = Field(None, description="Direct URL to the job listing")
    posted_date: Optional[str] = Field(None, description="Posted date as YYYY-MM-DD if visible")
    description: Optional[str] = Field(None, description="First 300 chars of job description")


# ── LLM config ────────────────────────────────────────────────────────────
def _build_llm_config() -> dict:
    if os.getenv("SGAI_API_KEY"):
        return {"model": "scrapegraphai/smart-scraper"}
    if os.getenv("GROQ_API_KEY"):
        return {"model": "groq/llama-3.1-8b-instant", "api_key": os.environ["GROQ_API_KEY"]}
    if os.getenv("OPENAI_API_KEY"):
        return {"model": "openai/gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]}
    if os.getenv("ANTHROPIC_API_KEY"):
        return {"model": "anthropic/claude-3-haiku-20240307",
                "api_key": os.environ["ANTHROPIC_API_KEY"], "model_tokens": 200000}
    if os.getenv("GEMINI_API_KEY"):
        return {"model": "google_genai/gemini-1.5-flash", "api_key": os.environ["GEMINI_API_KEY"]}
    raise RuntimeError(
        "ScrapeGraph: no LLM key. Add one of these to .env:\n"
        "  GROQ_API_KEY  (free — console.groq.com)\n"
        "  SGAI_API_KEY  (free credits — scrapegraphai.com)\n"
        "  OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY"
    )


# ── Tier 1: BeautifulSoup direct extraction ───────────────────────────────
def _bs4_extract(url: str) -> list[_JobItem] | None:
    """
    Fast, zero-token extraction for structured HTML job boards.

    Looks for department headings (e.g. 'Product', 'Product Management') and
    extracts all job links within that section. Returns None if the page doesn't
    have this structure (caller falls back to the LLM tier).
    """
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if r.status_code != 200:
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    base_domain = urlparse(url).netloc  # e.g. "job-boards.greenhouse.io"

    items: list[_JobItem] = []
    found_section = False

    for heading in soup.find_all(["h2", "h3", "h4"]):
        htext = heading.get_text(" ", strip=True).lower()
        if htext not in _PM_SECTION_KEYWORDS:
            continue

        found_section = True
        # Extract company from the heading's nearest parent with data- attribute,
        # or fall back to parsing the URL (e.g. /contentful/ -> "Contentful")
        path_parts = urlparse(url).path.strip("/").split("/")
        company_guess = path_parts[0].title() if path_parts else "Unknown"

        sib = heading.find_next_sibling()
        while sib and sib.name not in ["h2", "h3", "h4"]:
            for a in sib.find_all("a", href=True):
                job_url = a["href"]
                if not job_url.startswith("http"):
                    continue  # skip relative — shouldn't happen on Greenhouse

                # Anti-hallucination: only accept URLs on the same domain
                if urlparse(job_url).netloc != base_domain:
                    continue

                # The link text contains "Title + Location" concatenated
                full_text = a.get_text(" ", strip=True)
                # Greenhouse renders it as "<title><location>" in one <a>
                # Split on first city/country hint or just use full_text as title
                title = full_text
                location = None
                # Heuristic: if the text ends with a location pattern, split
                # (Greenhouse structure: title text followed by location text in nested spans)
                spans = a.find_all("span")
                if len(spans) >= 2:
                    title    = spans[0].get_text(" ", strip=True)
                    location = spans[-1].get_text(" ", strip=True)

                if title:
                    items.append(_JobItem(
                        title=title,
                        company=company_guess,
                        location=location,
                        url=job_url,
                    ))
            sib = sib.find_next_sibling()
        break  # only process the first matching section

    return items if found_section else None


# ── Tier 2: Scrapling stealth fetch + BS4 re-parse ───────────────────────
def _scrapling_extract(url: str) -> list[_JobItem] | None:
    """
    Re-fetch with a Chrome TLS fingerprint (curl-cffi) and run the same BS4
    parser on the result.  Returns None if Scrapling isn't installed or the
    fetch fails; returns an empty list if the page loaded but had no matching
    section (so the caller can skip the LLM tier for structurally simple pages
    that just don't have a product section).
    """
    try:
        from scrapling.fetchers import Fetcher
    except Exception:
        return None

    try:
        Fetcher.configure(auto_match=False)
        fetcher = Fetcher()
        response = fetcher.get(url, stealthy_headers=True, timeout=15)
        if not response or response.status not in range(200, 300):
            return None
        html = response.content if isinstance(response.content, str) else response.content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("Scrapling fetch failed for %s: %s", url, e)
        return None

    # Re-use the BS4 extractor on the freshly-fetched HTML
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(url).netloc

    items: list[_JobItem] = []
    found_section = False

    for heading in soup.find_all(["h2", "h3", "h4"]):
        htext = heading.get_text(" ", strip=True).lower()
        if htext not in _PM_SECTION_KEYWORDS:
            continue
        found_section = True
        path_parts = urlparse(url).path.strip("/").split("/")
        company_guess = path_parts[0].title() if path_parts else "Unknown"
        sib = heading.find_next_sibling()
        while sib and sib.name not in ["h2", "h3", "h4"]:
            for a in sib.find_all("a", href=True):
                job_url = a["href"]
                if not job_url.startswith("http"):
                    continue
                if urlparse(job_url).netloc != base_domain:
                    continue
                full_text = a.get_text(" ", strip=True)
                title = full_text
                location = None
                spans = a.find_all("span")
                if len(spans) >= 2:
                    title    = spans[0].get_text(" ", strip=True)
                    location = spans[-1].get_text(" ", strip=True)
                if title:
                    items.append(_JobItem(title=title, company=company_guess,
                                         location=location, url=job_url))
            sib = sib.find_next_sibling()
        break

    return items if found_section else None


# ── Tier 3: ScrapeGraphAI LLM extraction ─────────────────────────────────
def _llm_extract(url: str, llm_config: dict) -> list[_JobItem]:
    """
    LLM-based extraction for pages that can't be parsed with BeautifulSoup
    (custom career pages, Webflow sites, etc.). Falls back to empty list on error.
    """
    from scrapegraphai.graphs import SmartScraperGraph

    class _JobPage(BaseModel):
        jobs: list[_JobItem] = Field(description="All job listings found on the page")

    prompt = (
        "Extract ALL job listings visible on this page. "
        "For each job return: title, company, location, url (full absolute URL), "
        "posted_date (YYYY-MM-DD if shown, else null), description (first 300 chars). "
        "Only include real open positions. If a URL is not clearly stated, omit it."
    )
    config = {"llm": llm_config, "headless": True, "verbose": False}

    try:
        graph = SmartScraperGraph(prompt=prompt, source=url, config=config, schema=_JobPage)
        result = graph.run()
    except Exception as e:
        logger.warning("LLM extraction failed for %s: %s", url, e)
        return []

    base_domain = urlparse(url).netloc
    out = []
    for raw in (result.get("jobs") or []):
        try:
            item = _JobItem(**raw) if isinstance(raw, dict) else raw
            # Anti-hallucination: reject URLs pointing to different domains
            if item.url and urlparse(item.url).netloc not in (base_domain, ""):
                item = item.model_copy(update={"url": None})
            out.append(item)
        except Exception:
            pass
    return out


# ── Public entry point ────────────────────────────────────────────────────
def fetch(template: "JobSearchTemplate") -> list[Job]:
    """
    Scrape jobs from template.scrapegraph_urls list.

    Add career page URLs to your template JSON:
        "scrapegraph_urls": [
            "https://job-boards.greenhouse.io/mycompany",
            "https://example.com/careers"
        ]
    """
    urls: list[str] = getattr(template, "scrapegraph_urls", []) or []
    if not urls:
        logger.info("ScrapeGraph: no urls configured (add 'scrapegraph_urls' to template)")
        return []

    llm_config = None  # lazily resolved only if Tier 1 fails

    jobs: list[Job] = []
    seen: set[str] = set()

    for url in urls:
        logger.info("ScrapeGraph -> %s", url)

        # Tier 1: fast BeautifulSoup extraction
        items = _bs4_extract(url)
        if items is not None:
            logger.info("  [bs4] %d items extracted", len(items))
        else:
            # Tier 2: Scrapling stealth re-fetch + BS4 re-parse
            logger.info("  [scrapling] no structured section via plain HTTP, retrying with stealth fetch")
            items = _scrapling_extract(url)
            if items is not None:
                logger.info("  [scrapling] %d items extracted", len(items))
            else:
                # Tier 3: LLM fallback
                if llm_config is None:
                    try:
                        llm_config = _build_llm_config()
                    except RuntimeError as e:
                        logger.error(str(e))
                        return []
                provider = llm_config.get("model", "?").split("/")[0]
                logger.info("  [llm/%s] scrapling found no section, using LLM", provider)
                items = _llm_extract(url, llm_config)
                logger.info("  [llm] %d items extracted", len(items))

        for item in items:
            if not item.url or not item.url.startswith("http"):
                continue
            if item.url in seen:
                continue
            if not item.title:
                continue

            desc = item.description or ""
            if not is_product_role(item.title) or is_excluded(item.title, desc):
                continue

            seen.add(item.url)
            jobs.append(Job(
                title=item.title,
                company=item.company or "Unknown",
                location=item.location or template.location,
                url=item.url,
                source="ScrapeGraph",
                seniority=classify_seniority(item.title, desc),
                posted_date=item.posted_date,
                description=desc,
            ))

    logger.info("ScrapeGraph: %d jobs passed filters", len(jobs))
    return jobs
