#!/usr/bin/env python3
"""ProductLab Jobs — scrape free public job sources into SQLite.

Free and keyless. Collects from ~13 public sources, de-duplicates in
sqlite_writer, and (when CLAUDE_QA=1) runs a short Claude-CLI pass to clean
titles and flag duplicates/mismatches. No paid actors, no Notion, no fit model.

    python main.py --template <slug>
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

import classifier
import sqlite_writer
from template_loader import load_template, list_templates
from scrapers import (
    arbeitnow, arbeitsagentur, eures, greenhouse_api, ats_boards, careerjet,
    jobspy, rss_feeds, speedrun, vc_boards, foundhamburg, jsearch, scrapegraph,
)

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# (name, fetch, only_country). All free / keyless (JSearch is a capped free tier).
# only_country="DE" marks German/Berlin/Hamburg-specific sources that ignore the
# template location — they run only for German templates so they don't pollute a
# non-German city's DB.
SOURCES = [
    ("Arbeitnow",      arbeitnow.fetch,      "DE"),
    ("Arbeitsagentur", arbeitsagentur.fetch, "DE"),
    ("EURES",          eures.fetch,          None),
    ("Greenhouse",     greenhouse_api.fetch, None),
    ("ATS Boards",     ats_boards.fetch,     None),
    ("Careerjet",      careerjet.fetch,      None),
    ("JobSpy",         jobspy.fetch,         None),
    ("RSS Feeds",      rss_feeds.fetch,      "DE"),
    ("Speedrun",       speedrun.fetch,       None),
    ("VC Boards",      vc_boards.fetch,      None),
    ("FoundHamburg",   foundhamburg.fetch,   "DE"),
    ("JSearch",        jsearch.fetch,        None),
    ("ScrapeGraph",    scrapegraph.fetch,    None),
]


def _run(name, fn, template):
    log.info(f"Scraping {name}...")
    try:
        jobs = fn(template)
        log.info(f"  → {len(jobs)} jobs")
        return jobs
    except Exception as exc:
        log.error(f"  {name} failed: {exc}")
        return []


def _prompt_template():
    templates = list_templates()
    if not templates:
        print("No templates found. Add one under templates/ (see templates/example.json).")
        sys.exit(1)
    print("\nAvailable templates:")
    for i, (slug, name, desc) in enumerate(templates, 1):
        print(f"  [{i}] {name}")
    while True:
        try:
            n = int(input(f"\nSelect [1-{len(templates)}]: ").strip())
            if 1 <= n <= len(templates):
                return load_template(templates[n - 1][0])
        except (ValueError, KeyboardInterrupt):
            pass
        print("Invalid choice, try again.")


def main() -> None:
    parser = argparse.ArgumentParser(description="ProductLab Jobs — free job scraper")
    parser.add_argument("--template", metavar="SLUG", help="Template to use (e.g. berlin)")
    parser.add_argument("--dry-run", action="store_true", help="Print matched jobs without writing")
    parser.add_argument("--free-only", action="store_true",
                        help="(default behaviour; kept for compatibility)")
    args = parser.parse_args()

    template = load_template(args.template) if args.template else _prompt_template()
    log.info(f"Using template: {template.name}")
    classifier.set_active_template(template)

    cc = (template.country_code or "DE").upper()
    all_jobs = []
    for name, fn, only in SOURCES:
        if only and only != cc:
            continue
        all_jobs.extend(_run(name, fn, template))
    log.info(f"Total collected: {len(all_jobs)} jobs")

    if args.dry_run:
        for j in all_jobs:
            log.info(f"  [{j.source}] {j.seniority} | {j.title} @ {j.company} ({j.location})")
        return

    added, skipped, ids = sqlite_writer.write_jobs(all_jobs, template.slug)
    log.info(f"SQLite ({sqlite_writer.db_path()}). Added: {added} | Skipped: {skipped}")

    # Optional Claude-CLI QA pass over the new rows (clean titles, flag dups).
    if os.getenv("CLAUDE_QA") == "1" and ids:
        try:
            import claude_qa
            n = claude_qa.qa_job_ids(sqlite_writer.db_path(), ids)
            log.info(f"Claude QA: {n} new job(s) checked")
        except Exception as exc:
            log.warning(f"Claude QA skipped — {exc}")


if __name__ == "__main__":
    main()
