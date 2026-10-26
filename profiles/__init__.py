"""City registry — one entry per city the board serves. Each city has its own
SQLite database and search template, so data stays isolated per city (and can
later be consolidated into a single relational store without touching callers).
"""
from __future__ import annotations

REGISTRY: dict[str, dict] = {
    "berlin":    {"label": "Berlin",    "short": "Berlin",    "db": "jobs_berlin.db",    "templates": ["berlin"],    "country": "DE"},
    "amsterdam": {"label": "Amsterdam", "short": "Amsterdam", "db": "jobs_amsterdam.db", "templates": ["amsterdam"], "country": "NL"},
    "milan":     {"label": "Milan",     "short": "Milan",     "db": "jobs_milan.db",     "templates": ["milan"],     "country": "IT"},
    "barcelona": {"label": "Barcelona", "short": "Barcelona", "db": "jobs_barcelona.db", "templates": ["barcelona"], "country": "ES"},
    "warsaw":    {"label": "Warsaw",    "short": "Warsaw",    "db": "jobs_warsaw.db",    "templates": ["warsaw"],    "country": "PL"},
}

DEFAULT = "berlin"


def resolve(name: str | None) -> str:
    return name if name in REGISTRY else DEFAULT
