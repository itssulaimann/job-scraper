"""Company auditor — detects when the same real company was scraped under
different names (optionally by different scrapers), so the board data stays
clean and LinkedIn posts never list one employer twice.

Three signal tiers, cheapest first:
  1. exact normalized key   — "Deutsche Bahn" == "Deutsche Bahn AG"  (suffix/case)
  2. fuzzy (rapidfuzz)      — "Zalando" ~ "Zalando SE", typos, word order
  3. acronym / initialism   — "Deutsche Bahn" ?= "DB Systel"  (manual-review only)

Tiers 1–2 are merged into confident clusters; tier 3 is surfaced separately as
"possible aliases" for a human to confirm — never auto-merged, because initials
collide too easily to trust blindly.

Usable as a library (audit_db → JSON-safe dict for /api/audit) or a CLI:
    python3 company_audit.py jobs_pl.db
"""
from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict

try:
    from rapidfuzz import fuzz, process
    _HAS_RF = True
except Exception:                      # pragma: no cover - degrade gracefully
    _HAS_RF = False

# One canonical legal-suffix list (consolidates the three divergent copies that
# used to live in sqlite_writer / notion_writer / company_resolver).
_LEGAL = re.compile(
    r"\b(?:gmbh\s*(?:&|und)\s*co\.?\s*kg|gmbh|mbh|ag|se|kgaa|ug|inc|ltd|llc|"
    r"sarl|b\.?v\.?|n\.?v\.?|oy|a/?s|plc|s\.?a\.?|s\.?r\.?l|co|kg|e\.?\s*v)\b\.?",
    re.IGNORECASE,
)


def normalize_company(name: str | None) -> str:
    """Canonical comparison key: ascii-fold, lowercase, drop legal suffixes and
    punctuation. Conservative — it does NOT strip words like 'bank'/'group'
    (those merge distinct firms); fuzzy/acronym tiers handle the rest."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[&/|]", " ", s)
    s = _LEGAL.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _acronym(norm: str) -> str:
    words = [w for w in norm.split() if len(w) > 1 or w.isdigit()]
    return "".join(w[0] for w in words) if len(words) >= 2 else ""


class _UF:
    def __init__(self, items): self.p = {i: i for i in items}
    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb: self.p[rb] = ra


def audit_db(db_path: str, fuzzy_threshold: int = 90) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT company, source, COUNT(*) AS c FROM jobs "
        "WHERE trim(coalesce(company,'')) != '' GROUP BY company, source"
    ).fetchall()
    total_jobs  = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    missing_co  = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE trim(coalesce(company,'')) = ''").fetchone()[0]
    no_desc     = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE trim(coalesce(description,'')) = ''").fetchone()[0]
    dup_content = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT content_key) FROM jobs "
        "WHERE trim(coalesce(content_key,'')) != ''").fetchone()[0]
    conn.close()

    # aggregate per raw company name
    raw: dict[str, dict] = defaultdict(lambda: {"count": 0, "sources": defaultdict(int)})
    for r in rows:
        co = r["company"]
        raw[co]["count"] += r["c"]
        raw[co]["sources"][r["source"] or "—"] += r["c"]

    companies = list(raw.keys())
    norm = {c: normalize_company(c) for c in companies}

    uf = _UF(companies)
    # tier 1 — exact normalized key
    bykey: dict[str, list[str]] = defaultdict(list)
    for c in companies:
        if norm[c]:
            bykey[norm[c]].append(c)
    for group in bykey.values():
        for c in group[1:]:
            uf.union(group[0], c)

    # tier 2 — fuzzy merge of distinct normalized keys
    keys = [k for k in bykey if len(k) >= 4]        # skip ultra-short keys
    if _HAS_RF and len(keys) > 1:
        for k in keys:
            for mk, score, _ in process.extract(
                k, keys, scorer=fuzz.token_set_ratio, limit=5
            ):
                if mk != k and score >= fuzzy_threshold:
                    uf.union(bykey[k][0], bykey[mk][0])

    # assemble clusters
    groups: dict[str, list[str]] = defaultdict(list)
    for c in companies:
        groups[uf.find(c)].append(c)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        variants = sorted(
            ({"name": m, "count": raw[m]["count"], "sources": sorted(raw[m]["sources"])}
             for m in members),
            key=lambda v: -v["count"],
        )
        all_sources = sorted({s for m in members for s in raw[m]["sources"]})
        total = sum(v["count"] for v in variants)
        canonical = max(members, key=lambda m: (raw[m]["count"], -len(m)))
        clusters.append({
            "canonical":    canonical,
            "variants":     variants,
            "variant_count": len(variants),
            "sources":      all_sources,
            "cross_source": len(all_sources) >= 2,
            "total_jobs":   total,
        })
    clusters.sort(key=lambda x: (-x["variant_count"], -x["total_jobs"]))

    # tier 3 — acronym / initialism candidates (manual review only)
    acro_index: dict[str, list[str]] = defaultdict(list)
    for c in companies:
        a = _acronym(norm[c])
        if a:
            acro_index[a].append(c)
    possible = []
    seen_pairs = set()
    for c in companies:
        key = norm[c]
        if not key or " " in key or len(key) < 2:
            continue                      # single short token like "db", "sap"
        hits = [x for x in acro_index.get(key, []) if uf.find(x) != uf.find(c)]
        for h in hits:
            pair = tuple(sorted((uf.find(c), uf.find(h))))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            possible.append({
                "short":   c, "long": h,
                "note":    f"'{c}' matches the initials of '{h}'",
                "sources": sorted(set(raw[c]["sources"]) | set(raw[h]["sources"])),
            })

    cross = [c for c in clusters if c["cross_source"] and c["variant_count"] >= 2]

    return {
        "health": {
            "total_jobs":       total_jobs,
            "with_company":     total_jobs - missing_co,
            "missing_company":  missing_co,
            "missing_desc":     no_desc,
            "duplicate_rows":   max(0, dup_content),
            "distinct_raw":     len(companies),
            "distinct_clustered": len({uf.find(c) for c in companies}),
        },
        "cross_source_flags": cross,
        "clusters":           clusters,
        "possible_aliases":   possible,
        "fuzzy_available":    _HAS_RF,
    }


if __name__ == "__main__":
    import json
    path = sys.argv[1] if len(sys.argv) > 1 else "jobs_pl.db"
    res = audit_db(path)
    h = res["health"]
    print(f"DB: {path}")
    print(f"  jobs={h['total_jobs']}  companies raw={h['distinct_raw']} "
          f"→ clustered={h['distinct_clustered']}  "
          f"(missing company={h['missing_company']}, dup rows={h['duplicate_rows']})")
    print(f"  fuzzy: {'rapidfuzz' if res['fuzzy_available'] else 'EXACT ONLY (install rapidfuzz)'}")
    print(f"\nCross-source same-company flags: {len(res['cross_source_flags'])}")
    for c in res["cross_source_flags"][:25]:
        names = " | ".join(f"{v['name']} ({'/'.join(v['sources'])}:{v['count']})" for v in c["variants"])
        print(f"  • {c['canonical']}  →  {names}")
    if res["possible_aliases"]:
        print(f"\nPossible aliases (review): {len(res['possible_aliases'])}")
        for p in res["possible_aliases"][:15]:
            print(f"  ? {p['note']}")
