"""Profile registry — the board serves a single profile (its own SQLite DB and
template set). Kept as a tiny registry so the scrape/db plumbing stays generic.
"""
from __future__ import annotations

REGISTRY: dict[str, dict] = {
    "productlab": {
        "label": "ProductLab — Berlin",
        "short": "Berlin",
        "db": "jobs_pl.db",
        "templates": ["example"],
    },
}

DEFAULT = "productlab"


def resolve(name: str | None) -> str:
    return name if name in REGISTRY else DEFAULT
