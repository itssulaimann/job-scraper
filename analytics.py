"""Market analytics — the data-analyst report behind /analytics.

One pass over a profile's jobs DB, turned into the sections a report needs:
headline KPIs with week-over-week deltas, a 12-week intake trend, source
momentum, seniority / work-mode / city mix, top + newly-active companies,
role-theme keywords, posting freshness and day-of-week patterns.

All relative windows ("this week", "last week") are anchored to the most
recent scraped_date in the DB, not today(), so the report stays meaningful
even when the last scrape was a few days ago (and reads correctly in local
testing). `compute` takes the CITY/WORKTYPE CASE expressions from server.py
so the facets here always agree with the board's.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

import clean

# Generic title tokens that carry no "theme" signal — role nouns, gender tags,
# legal suffixes, locations, seniority words (those get their own chart).
_TITLE_STOP = {
    "product", "products", "manager", "managers", "management", "pm", "owner",
    "produkt", "produktmanager", "produktmanagerin", "produktmanagement",
    "lead", "leader", "senior", "junior", "mid", "head", "principal", "staff",
    "of", "the", "and", "for", "in", "to", "a", "an", "with", "at", "on", "or",
    "des", "der", "die", "und", "für", "im", "von", "zum", "zur", "bei", "mit",
    "m", "f", "d", "w", "x", "mwd", "mfd", "fmd", "gmbh", "ag", "se", "co", "kg",
    "remote", "hybrid", "onsite", "berlin", "germany", "deutschland", "münchen",
    "munich", "hamburg", "amsterdam", "milan", "milano", "barcelona", "warsaw",
    "eu", "full", "time", "part", "intern", "internship", "working", "student",
    "werkstudent", "all", "genders", "gender", "divers", "diverse", "international",
    "new", "join", "we", "are", "hiring", "role", "job", "jobs", "career", "team",
}

_WEEKS = 12            # length of the intake trend
_TOP_COMPANIES = 12
_TOP_KEYWORDS = 18
_MAX_NEW_COS = 16      # new-entrant chips to return


def _delta(cur: int, prev: int):
    """Percentage change, or None when there is no prior period to compare to."""
    if not prev:
        return None
    return round(100 * (cur - prev) / prev)


def compute(conn: sqlite3.Connection, city_sql: str, worktype_sql: str) -> dict:
    c = conn.cursor()
    ref = c.execute("SELECT MAX(scraped_date) FROM jobs").fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    if not ref or not total:
        return {"empty": True, "total": total or 0}

    ref_d = date.fromisoformat(ref[:10])
    d = lambda k: (ref_d - timedelta(days=k)).isoformat()

    def between(lo: str, hi: str) -> int:
        return c.execute(
            "SELECT COUNT(*) FROM jobs WHERE scraped_date >= ? AND scraped_date <= ?",
            [lo, hi]).fetchone()[0]

    # ── headline windows (anchored to the latest scrape) ──────────────────────
    week, week_prev = between(d(6), ref), between(d(13), d(7))
    month, month_prev = between(d(29), ref), between(d(59), d(30))

    active_cur = c.execute(
        "SELECT COUNT(DISTINCT company) FROM jobs "
        "WHERE company IS NOT NULL AND TRIM(company) != '' AND scraped_date >= ?",
        [d(6)]).fetchone()[0]
    active_prev = c.execute(
        "SELECT COUNT(DISTINCT company) FROM jobs WHERE company IS NOT NULL "
        "AND TRIM(company) != '' AND scraped_date >= ? AND scraped_date <= ?",
        [d(13), d(7)]).fetchone()[0]

    # ── 12-week intake trend (contiguous 7-day windows, gap-filled) ───────────
    intake_weekly = []
    for i in range(_WEEKS - 1, -1, -1):
        lo, hi = d(i * 7 + 6), d(i * 7)
        intake_weekly.append({"week": lo, "count": between(lo, hi)})

    # ── source momentum (case-folded so "LinkedIn"/"Linkedin" merge) ──────────
    srcmap: dict[str, dict] = {}
    for r in c.execute(
            "SELECT source, COUNT(*) total, SUM(scraped_date >= ?) new7 "
            "FROM jobs GROUP BY source", [d(6)]):
        raw = (r["source"] or "—").strip() or "—"
        g = srcmap.setdefault(raw.lower(), {"label": raw, "total": 0, "new7": 0, "_best": 0})
        g["total"] += r["total"]
        g["new7"] += r["new7"] or 0
        if r["total"] > g["_best"]:          # keep the most common casing as the label
            g["_best"], g["label"] = r["total"], raw
    by_source = sorted(srcmap.values(), key=lambda g: -g["total"])
    for g in by_source:
        g.pop("_best", None)
        g["share"] = round(100 * g["total"] / total)

    # ── categorical mixes ─────────────────────────────────────────────────────
    seniority = [{"label": r[0] or "Unspecified", "value": r[1]} for r in c.execute(
        "SELECT seniority, COUNT(*) FROM jobs GROUP BY seniority ORDER BY 2 DESC")]
    work_mode = [{"label": r[0], "value": r[1]} for r in c.execute(
        f"SELECT {worktype_sql} AS wt, COUNT(*) FROM jobs GROUP BY wt ORDER BY 2 DESC")]
    cities = [{"label": r[0], "value": r[1]} for r in c.execute(
        f"SELECT {city_sql} AS ct, COUNT(*) FROM jobs GROUP BY ct ORDER BY 2 DESC")]

    # ── companies: top hirers + new entrants, grouped by cleaned name ─────────
    cogroups: dict[str, dict] = {}
    for r in c.execute(
            "SELECT company, COUNT(*) n, SUM(scraped_date >= ?) new7, MIN(scraped_date) first "
            "FROM jobs WHERE company IS NOT NULL AND TRIM(company) != '' "
            "GROUP BY company", [d(6)]):
        label = clean.clean_company(r["company"]) or r["company"]
        g = cogroups.setdefault(label.casefold(),
                                {"label": label, "value": 0, "new7": 0, "first": r["first"]})
        g["value"] += r["n"]
        g["new7"] += r["new7"] or 0
        if r["first"] < g["first"]:
            g["first"] = r["first"]
    top_companies = [{"label": g["label"], "value": g["value"], "new7": g["new7"]}
                     for g in sorted(cogroups.values(), key=lambda g: -g["value"])[:_TOP_COMPANIES]]
    new_companies = sorted((g["label"] for g in cogroups.values() if g["first"] >= d(6)),
                           key=str.casefold)

    # ── posting freshness (posted → scraped lag) ──────────────────────────────
    lag = "julianday(scraped_date) - julianday(posted_date)"
    has_posted = "posted_date IS NOT NULL AND posted_date != ''"
    fr = c.execute(
        f"SELECT SUM({lag} <= 7), SUM({lag} > 7 AND {lag} <= 30), "
        f"SUM({lag} > 30 AND {lag} <= 90), SUM({lag} > 90), COUNT(*) "
        f"FROM jobs WHERE {has_posted}").fetchone()
    le7, b30, b90, old, with_posted = [x or 0 for x in fr]
    freshness = [
        {"label": "≤ 7 days", "value": le7},
        {"label": "8–30 days", "value": b30},
        {"label": "31–90 days", "value": b90},
        {"label": "> 90 days", "value": old},
    ]
    lags = [r[0] for r in c.execute(
        f"SELECT {lag} FROM jobs WHERE {has_posted} ORDER BY 1") if r[0] is not None]
    median_lag = round(lags[len(lags) // 2]) if lags else None

    # ── when roles are posted (day of week) ───────────────────────────────────
    dow_raw = {int(r[0]): r[1] for r in c.execute(
        f"SELECT strftime('%w', posted_date) w, COUNT(*) FROM jobs WHERE {has_posted} GROUP BY w")}
    dow_posting = [{"label": name, "value": dow_raw.get(idx, 0)} for name, idx in
                   (("Mon", 1), ("Tue", 2), ("Wed", 3), ("Thu", 4), ("Fri", 5), ("Sat", 6), ("Sun", 0))]

    # ── role-theme keywords (tokenized titles, stop-filtered) ─────────────────
    freq: dict[str, int] = {}
    for r in c.execute("SELECT clean_title, title FROM jobs"):
        for w in re.findall(r"[a-zA-ZäöüÄÖÜß]+", (r[0] or r[1] or "").lower()):
            if len(w) >= 3 and w not in _TITLE_STOP:
                freq[w] = freq.get(w, 0) + 1
    keywords = sorted(({"word": k, "value": v} for k, v in freq.items() if v >= 3),
                      key=lambda x: -x["value"])[:_TOP_KEYWORDS]

    return {
        "as_of": ref[:10],
        "total": total,
        "kpis": {
            "week": {"cur": week, "prev": week_prev, "delta": _delta(week, week_prev)},
            "month": {"cur": month, "prev": month_prev, "delta": _delta(month, month_prev)},
            "active_companies": {"cur": active_cur, "prev": active_prev,
                                 "delta": _delta(active_cur, active_prev)},
            "fresh_share": round(100 * le7 / with_posted) if with_posted else None,
            "median_lag": median_lag,
            "new_companies": len(new_companies),
        },
        "intake_weekly": intake_weekly,
        "by_source": by_source,
        "seniority": seniority,
        "work_mode": work_mode,
        "cities": cities,
        "top_companies": top_companies,
        "new_companies": new_companies[:_MAX_NEW_COS],
        "freshness": freshness,
        "fresh_unknown": total - with_posted,
        "dow_posting": dow_posting,
        "keywords": keywords,
    }
