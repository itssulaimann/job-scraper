#!/usr/bin/env python3
"""ProductLab Jobs — FastAPI backend for the public Berlin job board.

Serves the board, the LinkedIn-visual composer and the analytics/auditor page.
Reads a local SQLite DB populated by main.py. No Notion, fit model or triage.

    uvicorn server:app --host 0.0.0.0 --port 8642
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

import clean
import profiles

BASE = Path(__file__).parent
_DB_OVERRIDE = os.getenv("SQLITE_DB_PATH")


def db_for(profile: str) -> Path:
    if _DB_OVERRIDE:
        return Path(_DB_OVERRIDE)
    return BASE / profiles.REGISTRY[profile]["db"]


DB = db_for(profiles.DEFAULT)

app = FastAPI(title="ProductLab Jobs", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

# Optional visitor analytics — enabled only if tracking.py is present on the
# host (it is intentionally NOT in the public repo). No-op otherwise.
try:
    import tracking
    tracking.init(app)
except Exception:
    pass

# Per-IP rate limiting + security headers + body caps. Added last so it wraps
# everything and rejects abuse before any app logic (or the LLM) runs.
import security
security.init(app)

_jinja = Environment(
    loader=FileSystemLoader(str(BASE / "templates")),
    autoescape=select_autoescape(["html"]),
    cache_size=0,
    auto_reload=True,
)
_jinja.globals["asset_v"] = lambda path: int((BASE / path).stat().st_mtime)


def _render(name: str, request: Request, **ctx) -> HTMLResponse:
    return HTMLResponse(_jinja.get_template(name).render(request=request, **ctx))


# ── db helpers ───────────────────────────────────────────────────────────────

def _db(profile: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_for(profile) if profile else DB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(profile: str = None) -> None:
    with _db(profile) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, company TEXT, location TEXT, url TEXT NOT NULL,
                source TEXT, seniority TEXT, posted_date TEXT, scraped_date TEXT NOT NULL,
                description TEXT, template TEXT, content_key TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)


def _migrate(profile: str = None) -> None:
    with _db(profile) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        for c in ("content_key", "clean_title", "qa_flag"):
            if c not in cols:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {c} TEXT")
        if "qa_version" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN qa_version INTEGER")


for _p in profiles.REGISTRY:
    _ensure_table(_p)
    _migrate(_p)


# ── query building ───────────────────────────────────────────────────────────

# Collapse freeform location strings into a stable city facet (shared by filter
# and stats so they agree).
CITY_SQL = """CASE
  WHEN lower(coalesce(location,'')) LIKE '%berlin%' THEN 'Berlin'
  WHEN lower(coalesce(location,'')) LIKE '%münchen%'
    OR lower(coalesce(location,'')) LIKE '%munich%'
    OR lower(coalesce(location,'')) LIKE '%bayern%' THEN 'Munich'
  WHEN lower(coalesce(location,'')) LIKE '%hamburg%' THEN 'Hamburg'
  WHEN lower(coalesce(location,'')) LIKE '%remote%'
    OR lower(coalesce(location,'')) LIKE '%homeoffice%' THEN 'Remote'
  ELSE 'Elsewhere'
END"""

SORTS = {
    "posted":  "posted_date IS NULL, posted_date DESC, scraped_date DESC, id DESC",
    "newest":  "scraped_date DESC, id DESC",
    "oldest":  "scraped_date ASC, id ASC",
    "company": "company COLLATE NOCASE ASC, scraped_date DESC",
}


def _build_where(q: str, source: str, seniority: str, city: str, days: int):
    clauses, params = ["1=1"], []
    if q:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ?)")
        params += [f"%{q.lower()}%", f"%{q.lower()}%"]
    for raw, col in ((source, "source"), (seniority, "seniority"), (city, CITY_SQL)):
        if raw:
            items = [s.strip() for s in raw.split(",") if s.strip()]
            if items:
                clauses.append(f"{col} IN ({','.join('?' * len(items))})")
                params += items
    if days > 0:
        clauses.append("scraped_date >= ?")
        params.append((date.today() - timedelta(days=days)).isoformat())
    return " AND ".join(clauses), params


def _job_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["title_display"]   = (d.get("clean_title") or "").strip() or clean.clean_title(d.get("title"))
    d["company_display"] = clean.clean_company(d.get("company"))
    d["work_type"]       = clean.work_type(d.get("title"), d.get("location"), d.get("description"))
    d.pop("description", None)
    return d


# ── pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return _render("jobs.html", request)


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    return _render("analytics.html", request)


@app.get("/compose", response_class=HTMLResponse)
async def compose_page(request: Request):
    return _render("compose.html", request)


# Discovery files for LLMs / crawlers (served at the site root).
@app.get("/llms.txt", include_in_schema=False)
@app.get("/llm.txt", include_in_schema=False)
async def llms_txt():
    try:
        return PlainTextResponse((BASE / "llms.txt").read_text(encoding="utf-8"))
    except Exception:
        return PlainTextResponse("", status_code=404)


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    return PlainTextResponse("User-agent: *\nAllow: /\n\n# LLM discovery: /llms.txt\n")


# ── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
async def list_jobs(q: str = "", source: str = "", seniority: str = "", city: str = "",
                    days: int = 0, sort: str = "posted", page: int = 1,
                    per_page: int = 40, profile: str = ""):
    profile = profiles.resolve(profile)
    where, params = _build_where(q, source, seniority, city, days)
    order = SORTS.get(sort, SORTS["posted"])
    offset = (page - 1) * per_page
    with _db(profile) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT *, {CITY_SQL} AS city FROM jobs WHERE {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?", params + [per_page, offset]
        ).fetchall()
    return {
        "total": total, "page": page, "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "jobs": [_job_dict(r) for r in rows],
    }


@app.get("/api/stats")
async def get_stats(profile: str = ""):
    profile = profiles.resolve(profile)
    today_iso = date.today().isoformat()
    week_iso = (date.today() - timedelta(days=7)).isoformat()
    with _db(profile) as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        today_n = conn.execute("SELECT COUNT(*) FROM jobs WHERE scraped_date = ?", [today_iso]).fetchone()[0]
        week_n = conn.execute("SELECT COUNT(*) FROM jobs WHERE scraped_date >= ?", [week_iso]).fetchone()[0]
        cos = conn.execute("SELECT COUNT(DISTINCT company) FROM jobs WHERE company IS NOT NULL").fetchone()[0]
        by_source = [dict(r) for r in conn.execute(
            "SELECT source AS label, COUNT(*) AS value FROM jobs GROUP BY source ORDER BY value DESC")]
        by_date = [dict(r) for r in conn.execute(
            "SELECT scraped_date AS date, COUNT(*) AS count FROM jobs GROUP BY scraped_date ORDER BY scraped_date")]
        top_cos = [dict(r) for r in conn.execute(
            "SELECT company AS label, COUNT(*) AS value FROM jobs WHERE company IS NOT NULL "
            "GROUP BY company ORDER BY value DESC LIMIT 15")]
    return {"total": total, "today": today_n, "week": week_n, "companies": cos,
            "by_source": by_source, "by_date": by_date, "top_companies": top_cos}


@app.get("/api/audit")
async def api_audit(profile: str = ""):
    import company_audit
    return company_audit.audit_db(str(db_for(profiles.resolve(profile))))


@app.get("/api/profiles")
async def api_profiles():
    return {"profiles": profiles.REGISTRY, "default": profiles.DEFAULT}


# ── scrape (admin-gated) ──────────────────────────────────────────────────────

_scrape_status: dict = {"running": False, "profile": None, "log": [], "result": None}


@app.get("/api/scrape/status")
async def scrape_status():
    return _scrape_status


@app.post("/api/scrape")
async def start_scrape(payload: dict = None):
    # Manual fetching is gated by a shared password (FETCH_PASSWORD). Unset =
    # fully disabled; the daily timer refreshes the board instead. Validated
    # server-side so the password never ships to the browser.
    expected = os.getenv("FETCH_PASSWORD", "")
    if not expected or (payload or {}).get("password", "") != expected:
        return JSONResponse({"error": "Manual fetching is disabled."}, status_code=403)

    profile = profiles.resolve((payload or {}).get("profile", ""))
    if _scrape_status["running"]:
        return JSONResponse(
            {"error": "A scrape is already running", "profile": _scrape_status["profile"]},
            status_code=409)
    _scrape_status.update(running=True, profile=profile, log=[], result=None)
    templates = profiles.REGISTRY[profile]["templates"]
    db_path = str(db_for(profile))

    async def _run():
        total_added = 0
        try:
            for slug in templates:
                _scrape_status["log"].append(f"Running template: {slug}")
                env = {**os.environ, "SQLITE_DB_PATH": db_path}
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(BASE / "main.py"), "--template", slug, "--free-only",
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                stdout, _ = await proc.communicate()
                for ln in (stdout or b"").decode(errors="replace").splitlines():
                    if "Added:" in ln:
                        _scrape_status["log"].append(ln.strip())
                        try:
                            total_added += int(ln.split("Added:")[1].split("|")[0].strip())
                        except (ValueError, IndexError):
                            pass
                    elif "[ERROR]" in ln:
                        _scrape_status["log"].append(ln.strip())
            _scrape_status["result"] = {"ok": True, "added": total_added}
        except Exception as exc:
            _scrape_status["result"] = {"ok": False, "error": str(exc)}
        finally:
            _scrape_status["running"] = False

    asyncio.create_task(_run())
    return {"started": True, "profile": profile, "templates": templates}
