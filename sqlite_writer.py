"""SQLite persistence layer — mirrors every scraped job into a local jobs.db file.

No configuration needed. The database and table are created automatically on first run.
Default location: <project_root>/jobs.db
Override with SQLITE_DB_PATH in .env if needed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import unicodedata
from datetime import date, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from scrapers.base import Job

log = logging.getLogger(__name__)

# Days within which the same title+company is treated as the same posting.
# Mirrors notion_writer so the dashboard and Notion agree on what a duplicate
# is; a genuine re-post after this window is allowed back in.
_CONTENT_DEDUP_DAYS = 30

# ------------------------------------------------------------------ helpers

_STRIP_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "referer",
    "refId", "trackingId", "position", "pageNum", "trk", "trkInfo",
    "vjk", "fccid", "from", "sa", "hl",
    "cid", "sid",
})


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        qs = {k: v for k, v in parse_qs(p.query, keep_blank_values=False).items()
              if k not in _STRIP_PARAMS}
        return urlunparse((
            p.scheme.lower(), p.netloc.lower(),
            p.path.rstrip("/"), p.params,
            urlencode(qs, doseq=True), "",
        ))
    except Exception:
        return url


def job_id(url: str) -> str:
    """SHA-256 hash of the normalised URL — same key used by Notion Job ID."""
    return hashlib.sha256(_normalize_url(url).encode()).hexdigest()[:32]


_LEGAL_SUFFIX = re.compile(
    r"\b(gmbh\s*(&|und)\s*(co\.?\s*)?kg|gmbh|ag|se|inc\.?|ltd\.?|llc|sarl|bv|nv"
    r"|oy|as|plc|sa)\b",
    re.IGNORECASE,
)


def content_key(title: str, company: str) -> str:
    """normalize(title)|normalize(company) — catches the same real posting
    arriving under different URLs.

    This matters more than URL dedup for aggregators: Careerjet mints a fresh
    jobviewtrack.com token and Arbeitnow a fresh numeric id on every scrape, so
    the URL hash never repeats and the same job lands again and again.
    """
    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
        s = s.lower()
        s = _LEGAL_SUFFIX.sub("", s)
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        return re.sub(r"\s+", " ", s).strip()
    return _norm(title) + "|" + _norm(company)


def db_path() -> str:
    return os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(__file__), "jobs.db"))


# ------------------------------------------------------------------ schema

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    company      TEXT,
    location     TEXT,
    url          TEXT NOT NULL,
    source       TEXT,
    seniority    TEXT,
    posted_date  TEXT,
    scraped_date TEXT NOT NULL,
    description  TEXT,
    template     TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
"""

_MIGRATE = [
    "ALTER TABLE jobs ADD COLUMN content_key TEXT",
    "CREATE INDEX IF NOT EXISTS idx_jobs_content_key ON jobs(content_key)",
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "content_key" not in cols:
        conn.execute(_MIGRATE[0])
    conn.execute(_MIGRATE[1])
    # llm_fit_version is normally added by llm_fit_scorer.py / server.py, but
    # write_jobs() resets it below on a description upgrade — on a brand new
    # DB this file may run first, so add it defensively rather than assume.
    if "llm_fit_version" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN llm_fit_version INTEGER")

# ------------------------------------------------------------------ write


def write_jobs(jobs: list[Job], template_slug: str = "") -> tuple[int, int, list[int]]:
    """
    INSERT OR IGNORE all jobs into the local SQLite DB.
    Creates the DB file and table automatically if they don't exist.
    Returns (inserted, skipped, ids_to_score) — ids_to_score covers brand-new
    rows plus existing rows whose description was just enriched (their
    fit/lang/LLM scores are reset below so they get recomputed on the richer
    text), for the caller to hand straight to llm_fit_scorer.score_job_ids().
    """
    path = db_path()
    today = date.today().isoformat()
    inserted = skipped = 0
    ids_to_score: list[int] = []

    cutoff = (date.today() - timedelta(days=_CONTENT_DEDUP_DAYS)).isoformat()

    try:
        with sqlite3.connect(path) as conn:
            _ensure_schema(conn)
            for job in jobs:
                if not job.url:
                    skipped += 1
                    continue
                jid = job_id(job.url)
                ckey = content_key(job.title, job.company or "")
                desc = job.description or None
                try:
                    # Content-level dedup. Aggregators re-mint URLs, so the URL
                    # hash alone lets the same posting in repeatedly.
                    dup = conn.execute(
                        "SELECT id, LENGTH(COALESCE(description,'')) FROM jobs "
                        "WHERE content_key = ? AND scraped_date >= ? LIMIT 1",
                        (ckey, cutoff),
                    ).fetchone() if ckey.strip("|") else None

                    if dup:
                        # Keep the richest copy: a teaser from an aggregator can
                        # omit the very lines the fit/language filters read.
                        if desc and len(desc) > (dup[1] or 0):
                            conn.execute(
                                "UPDATE jobs SET description = ?, "
                                "lang_version = NULL, llm_fit_version = NULL WHERE id = ?",
                                (desc, dup[0]),
                            )
                            ids_to_score.append(dup[0])
                        skipped += 1
                        continue

                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO jobs
                            (job_id, title, company, location, url, source,
                             seniority, posted_date, scraped_date, description,
                             template, content_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            jid,
                            job.title[:500],
                            job.company or None,
                            job.location or None,
                            job.url,
                            job.source,
                            job.seniority,
                            job.posted_date or None,
                            today,
                            desc,
                            template_slug or None,
                            ckey,
                        ),
                    )
                    if cur.rowcount:
                        inserted += 1
                        ids_to_score.append(cur.lastrowid)
                    else:
                        skipped += 1
                except Exception as exc:
                    log.error(f"SQLite insert failed for '{job.title}': {exc}")
                    skipped += 1
    except Exception as exc:
        log.error(f"SQLite write failed: {exc}")

    return inserted, skipped, ids_to_score
