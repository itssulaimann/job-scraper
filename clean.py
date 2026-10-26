"""Lightweight, deterministic title/company cleaning for the v2 board + visuals.

This is the *fallback* layer: it runs live on every row so the board always
shows a tidy title even before the Claude QA pass has touched a job. When the
Claude QA pass has written a `clean_title`, the API prefers that (see server.py).

Conservative on purpose — strip obvious noise (gender tags, work-mode
parentheticals, aggregator junk, legal suffixes for display) without mangling
the real role title.
"""
from __future__ import annotations

import re

# (m/w/d) (w/m/d) (m/f/x) (gn) (all genders) (d/f/m) … and the bare m/w/d form
_GENDER = re.compile(
    r"""\(?\s*(?:
        [mwfdxa](?:\s*[/|]\s*[mwfdxa]){1,2}        # m/w/d, w/m/x, m/f/d/x …
      | all\s+genders | alle\s+geschlechter
      | gn | m\s*/\s*f | f\s*/\s*m | divers
    )\s*\)?""",
    re.IGNORECASE | re.VERBOSE,
)

# work-mode parentheticals we lift out of the title into work_type
_WORKMODE_IN_TITLE = re.compile(
    r"\(?\s*(?:homeoffice(?:\s+möglich)?|home\s*office|remote|hybrid|vollzeit|teilzeit|full[-\s]?time|part[-\s]?time)\s*\)?",
    re.IGNORECASE,
)

# aggregator / board junk that trails many scraped titles
_BOARD_JUNK = re.compile(
    r"""(?:
        [.\-–—|:]*\s*Job\s+in\s+.+$          # "… Job in Berlin Gecko Jobs"
      | \s*[-–—|]\s*(?:Indeed|StepStone|LinkedIn|Glassdoor|Xing|Careerjet|Gecko\s*Jobs?)(?:\.com)?\s*$
      | \s*\b(?:Gecko\s*Jobs?)\b\s*$
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_LEGAL = re.compile(
    r"\b(?:gmbh\s*(?:&|und)\s*co\.?\s*kg|gmbh|mbh|ag|se|kgaa|inc\.?|ltd\.?|llc|"
    r"sarl|b\.?v\.?|n\.?v\.?|oy|a/?s|plc|s\.?a\.?|e\.?v\.?)\b\.?",
    re.IGNORECASE,
)

_MULTISPACE = re.compile(r"\s{2,}")
_TRAIL_PUNCT = re.compile(r"[\s,;:\-–—/|]+$")
_LEAD_PUNCT = re.compile(r"^[\s,;:\-–—/|]+")


def clean_title(title: str | None) -> str:
    """Tidy a scraped job title for display. Idempotent and conservative."""
    if not title:
        return ""
    t = title.strip()
    t = _BOARD_JUNK.sub("", t)
    t = _GENDER.sub("", t)
    t = _WORKMODE_IN_TITLE.sub("", t)
    # strip a trailing ", Berlin" / "– Berlin" (city implied by the whole board)
    t = re.sub(r"\s*[,\-–—|]\s*berlin\s*$", "", t, flags=re.IGNORECASE)
    t = _TRAIL_PUNCT.sub("", _LEAD_PUNCT.sub("", t))
    t = _MULTISPACE.sub(" ", t).strip()
    # collapse an orphaned " / " left behind by removals  ("Manager /  ")
    t = re.sub(r"\s*/\s*$", "", t).strip()
    return t or title.strip()


def clean_company(company: str | None, *, drop_legal: bool = True) -> str:
    """Tidy a company name for display (optionally drop the legal suffix)."""
    if not company:
        return ""
    c = company.strip()
    c = re.sub(r"\s*\|\s*.*$", "", c)          # "DKB | Deutsche Kreditbank AG" -> "DKB"? no—keep first
    if drop_legal:
        c = _LEGAL.sub("", c)
    c = _TRAIL_PUNCT.sub("", _MULTISPACE.sub(" ", c)).strip()
    return c or company.strip()


def work_type(title: str | None, location: str | None, description: str | None) -> str:
    """Best-effort On-site / Hybrid / Remote from the available text."""
    blob = " ".join(x for x in (title, location, description) if x).lower()
    if not blob:
        return "On-site"
    if "hybrid" in blob:
        return "Hybrid"
    if re.search(r"\b(fully remote|100%\s*remote|remote[- ]first|remote)\b", blob) \
       or "home office" in blob or "homeoffice" in blob:
        # a bare "remote" plus an on-site city often means hybrid; keep it simple
        return "Remote"
    return "On-site"
