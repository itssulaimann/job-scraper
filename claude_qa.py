"""Post-scrape QA using the Claude Code CLI installed on the server.

After each scrape, main.py hands the *new* rows to `qa_job_ids`. For each batch
we shell out to `claude -p` (the account already signed in on the box) and ask
it to (1) produce a clean, human-readable title and (2) flag obvious problems —
a title/description mismatch or staffing spam. Results are written to
jobs.clean_title / jobs.qa_flag; nothing is ever deleted.

Non-destructive, capped, and fail-safe: if the CLI is missing, errors, or times
out, the scrape is unaffected — rows just keep their regex-cleaned title.

Env:
  CLAUDE_BIN   path to the claude binary (default /home/claude/.local/bin/claude)
  CLAUDE_QA_MAX   max jobs to QA per run (default 60)
  CLAUDE_QA_BATCH per-call batch size (default 8)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess

log = logging.getLogger(__name__)

QA_VERSION = 1
_BIN      = os.getenv("CLAUDE_BIN", "/home/claude/.local/bin/claude")
_MAX      = int(os.getenv("CLAUDE_QA_MAX", "60"))
_BATCH    = int(os.getenv("CLAUDE_QA_BATCH", "8"))
_TIMEOUT  = int(os.getenv("CLAUDE_QA_TIMEOUT", "120"))
_MAX_FAILS = 3

_PROMPT = """You are cleaning scraped job listings for a Berlin job board. \
For EACH job below return a tidy, human-readable job TITLE and a quality FLAG.

clean_title rules: remove gender tags like (m/w/d)/(w/m/d)/(all genders); remove \
recruiter/aggregator junk like "Job in Berlin Gecko Jobs" or "- Indeed.com"; \
remove trailing location/company noise; keep the real role wording; do NOT translate.

flag: "ok" normally; "mismatch" if the title clearly does not match the role \
described; "spam" if it's staffing-agency filler or not a real single job.

Return ONLY a compact JSON array, no prose, no markdown fences, exactly like:
[{"id":1,"clean_title":"Senior Product Manager","flag":"ok"}]

Jobs:
"""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _call_claude(prompt: str) -> str:
    """Run `claude -p` via a login shell (so ~/.local/bin is on PATH)."""
    proc = subprocess.run(
        ["bash", "-lc", f'{_BIN} -p {json_quote(prompt)} --max-turns 1'],
        capture_output=True, text=True, timeout=_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:200]}")
    return proc.stdout.strip()


def json_quote(s: str) -> str:
    """Single-quote-safe shell arg."""
    return "'" + s.replace("'", "'\\''") + "'"


def _parse(out: str) -> list[dict]:
    out = _FENCE.sub("", out).strip()
    start, end = out.find("["), out.rfind("]")
    if start == -1 or end == -1:
        return []
    return json.loads(out[start:end + 1])


def qa_job_ids(db_path: str, ids: list[int]) -> int:
    """QA up to CLAUDE_QA_MAX of the given job ids. Returns #rows updated."""
    if not ids:
        return 0
    ids = ids[:_MAX]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT id, title, description FROM jobs WHERE id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()

    updated, fails = 0, 0
    for i in range(0, len(rows), _BATCH):
        batch = rows[i:i + _BATCH]
        payload = [
            {"id": r["id"], "title": (r["title"] or "")[:200],
             "desc": (r["description"] or "")[:350]}
            for r in batch
        ]
        try:
            out = _call_claude(_PROMPT + json.dumps(payload, ensure_ascii=False))
            results = _parse(out)
        except Exception as exc:
            fails += 1
            log.warning(f"Claude QA batch failed ({fails}/{_MAX_FAILS}): {exc}")
            if fails >= _MAX_FAILS:
                log.warning("Claude QA circuit breaker tripped — stopping.")
                break
            continue

        by_id = {r["id"]: r for r in batch}
        for item in results:
            try:
                jid = int(item["id"])
            except (KeyError, ValueError, TypeError):
                continue
            if jid not in by_id:
                continue
            ct = (item.get("clean_title") or "").strip()[:300]
            fl = (item.get("flag") or "ok").strip().lower()[:20]
            conn.execute(
                "UPDATE jobs SET clean_title = ?, qa_flag = ?, qa_version = ? WHERE id = ?",
                (ct or None, fl, QA_VERSION, jid),
            )
            updated += 1
        conn.commit()

    conn.close()
    return updated


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    path = sys.argv[1] if len(sys.argv) > 1 else "jobs_pl.db"
    # QA the newest 16 rows as a smoke test
    c = sqlite3.connect(path)
    ids = [r[0] for r in c.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 16")]
    c.close()
    print(f"QA'ing {len(ids)} rows in {path} …")
    print("updated:", qa_job_ids(path, ids))
