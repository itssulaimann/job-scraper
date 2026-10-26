from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from template_loader import JobSearchTemplate

# ------------------------------------------------------------------ defaults
# These are used when no template is active (e.g. tests, one-off scripts).

ALLOWED_TITLE_TERMS = [
    "product manager", "product owner", "product analyst", "product lead",
    "product director", "product management", "product builder",
    "head of product", "vp product", "vp of product",
    "vice president product", "vice president of product",
    "chief product", "director of product", "group product manager",
    "principal product manager", "technical product manager",
    "produktmanager", "produktmanagement", "produktowner",
    "leiter produkt", "leitung produkt",
]

EXCLUDE_KEYWORDS = [
    "intern", "internship", "student", "werkstudent", "werkstudentin",
    "praktikum", "praktikant", "praktikantin", "thesis", "bachelor thesis",
    "master thesis", "duales studium", "trainee", "apprentice",
]

_EXCLUDE_PATTERNS = [
    re.compile(r"\b" + re.escape(kw) + r"\b") for kw in EXCLUDE_KEYWORDS
]

# Ordered: first match wins — Director must come before Senior
SENIORITY_RULES = [
    (
        ["director", "head of product", "vp product", "vp of product",
         "vice president product", "vice president of product",
         "chief product", "cpo", "group product manager", "gpm"],
        "Director",
    ),
    (["senior", "lead", "principal", "staff", "sr."], "Senior PM"),
    (["junior", "associate", "entry level", "graduate", "jr.", "jr "], "Junior PM"),
]

# ------------------------------------------------------------------ active template

_template: Optional[JobSearchTemplate] = None
_active_exclude_patterns: list[re.Pattern] = _EXCLUDE_PATTERNS


def set_active_template(t: JobSearchTemplate) -> None:
    global _template, _active_exclude_patterns
    _template = t
    _active_exclude_patterns = [
        re.compile(r"\b" + re.escape(kw) + r"\b") for kw in t.exclude_keywords
    ]


def get_active_template() -> Optional[JobSearchTemplate]:
    return _template


# ------------------------------------------------------------------ filter functions

def is_recent(posted_date: str | None, max_hours: int = 48) -> bool:
    """True if posted within max_hours, or if date is unknown."""
    if not posted_date:
        return True
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_hours)).date()
        return date.fromisoformat(posted_date[:10]) >= cutoff
    except Exception:
        return True


def is_product_role(title: str) -> bool:
    """True if the title matches the active template's allowlist."""
    terms = _template.allowed_title_terms if _template else ALLOWED_TITLE_TERMS
    t = title.lower()
    return any(re.search(r"\b" + re.escape(term) + r"\b", t) for term in terms)


def is_excluded(title: str, description: str = "") -> bool:
    text = (title + " " + description).lower()
    return any(pat.search(text) for pat in _active_exclude_patterns)


def classify_seniority(title: str, description: str = "") -> str:
    if _template:
        rules = [([kw.lower() for kw in r.keywords], r.level) for r in _template.seniority_rules]
        default = _template.seniority_default
    else:
        rules = SENIORITY_RULES
        default = "Senior PM"
    text = (title + " " + description[:300]).lower()
    for keywords, level in rules:
        if any(kw in text for kw in keywords):
            return level
    return default


