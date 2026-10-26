"""
Template system for JobFinder.

Each template is a JSON file in templates/ that defines what role to search for,
which platforms to query, how to filter results, and which Notion database to write to.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_DB_ID = ""


@dataclass
class SeniorityRule:
    keywords: list[str]
    level: str


@dataclass
class JobSearchTemplate:
    # Display
    name: str
    description: str
    slug: str

    # Location
    location: str                       # e.g. "Berlin"
    country_code: str                   # e.g. "DE"
    linkedin_location_encoded: str      # e.g. "Berlin%2C+Germany"
    location_keywords: list[str]        # for Arbeitnow local filter e.g. ["berlin"]

    # Search terms per platform
    linkedin_keywords: list[str]        # one URL built per keyword
    xing_keyword: str
    indeed_position: str
    stepstone_query: str
    wellfound_role_slug: str            # e.g. "product-manager"
    arbeitsagentur_queries: list[str]

    # ATS direct monitoring
    greenhouse_slugs: list[str]

    # Title filtering
    allowed_title_terms: list[str]
    exclude_keywords: list[str]

    # Seniority classification
    seniority_rules: list[SeniorityRule]
    seniority_default: str              # label when no rule matches

    # Output
    notion_db_id: str = DEFAULT_DB_ID

    # ScrapeGraphAI — career pages / niche boards to scrape with AI
    scrapegraph_urls: list[str] = field(default_factory=list)

    # EURES (EU public job network).
    #   eures_location_codes  — NUTS-1 codes to query, lowercase (e.g. ["de3"]
    #                           = Berlin, ["de2"] = Bayern). Deeper levels are
    #                           rejected by the API, so filter client-side.
    #   eures_nuts_prefixes   — optional client-side narrowing, uppercase
    #                           (e.g. ["DE21"] = Oberbayern, for Munich).
    eures_location_codes: list[str] = field(default_factory=list)
    eures_nuts_prefixes: list[str] = field(default_factory=list)

    # Careerjet free public API — plain city name, e.g. "Berlin"
    careerjet_location: str = ""

    # JSearch (Google for Jobs aggregate). Free-form queries; the country code
    # is taken from country_code. Each query page costs one API credit.
    jsearch_queries: list[str] = field(default_factory=list)

    # ATS boards keyed by company slug
    smartrecruiters_companies: list[str] = field(default_factory=list)
    ashby_slugs: list[str] = field(default_factory=list)
    personio_slugs: list[str] = field(default_factory=list)
    lever_slugs: list[str] = field(default_factory=list)
    workable_slugs: list[str] = field(default_factory=list)

    # VC portfolio boards on Getro — [{"name": "Cherry Ventures", "id": 44081}].
    # One entry covers a whole portfolio, so no per-company slugs are needed.
    # Find an id by loading the board (e.g. https://cherry.getro.com) and
    # reading the numeric id out of the embedded JSON.
    getro_networks: list[dict] = field(default_factory=list)
    getro_queries: list[str] = field(default_factory=list)

    # Schedule (standard 5-field cron, UTC)
    cron_schedule: str = "0 21 * * 1-5"  # Mon–Fri 21:00 UTC ≈ 23:00 Berlin

    # ------------------------------------------------------------------ helpers

    @classmethod
    def from_dict(cls, d: dict) -> JobSearchTemplate:
        d = dict(d)
        rules = [SeniorityRule(**r) for r in d.pop("seniority_rules", [])]
        # Ignore keys the dataclass doesn't define, so an older template (or a
        # hand-added note field) doesn't crash the whole run.
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            logger.debug("Template '%s': ignoring unknown keys %s",
                         d.get("slug", "?"), sorted(unknown))
        return cls(seniority_rules=rules, **{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        out = {k: v for k, v in self.__dict__.items() if k != "seniority_rules"}
        out["seniority_rules"] = [
            {"keywords": r.keywords, "level": r.level} for r in self.seniority_rules
        ]
        return out


# ------------------------------------------------------------------ public API

def load_template(slug: str) -> JobSearchTemplate:
    """Load a template by its slug (filename without .json)."""
    path = TEMPLATES_DIR / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Template '{slug}' not found.\n"
            f"Available: {[f.stem for f in TEMPLATES_DIR.glob('*.json')]}"
        )
    return JobSearchTemplate.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_templates() -> list[tuple[str, str, str]]:
    """Return list of (slug, name, description) for all available templates."""
    result = []
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            result.append((f.stem, d.get("name", f.stem), d.get("description", "")))
        except Exception:
            pass
    return result


def save_template(template: JobSearchTemplate) -> Path:
    """Persist a template to templates/<slug>.json."""
    TEMPLATES_DIR.mkdir(exist_ok=True)
    path = TEMPLATES_DIR / f"{template.slug}.json"
    path.write_text(
        json.dumps(template.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")
