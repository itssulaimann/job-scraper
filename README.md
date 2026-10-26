# ProductLab Jobs

A free, open board of fresh **Product Manager** roles across European tech hubs —
**Berlin, Amsterdam, Milan, Barcelona and Warsaw**. Listings are scraped daily from
~13 free, public sources, de-duplicated (including the same employer listed under
slightly different names), cleaned, and served as a fast web board with a per-city
analytics view and a LinkedIn-post composer. No login, no Notion, no paid APIs required.

## How it works

```
main.py (scrape) → SQLite (one DB per city) → server.py (FastAPI board + analytics + composer)
```

- **Scrapers** (`scrapers/`) pull from free, keyless public endpoints.
- **`company_audit.py`** clusters near-duplicate company names (fuzzy) and flags the
  same role found across multiple sources.
- **`clean.py`** normalises titles, companies and work-type (on-site / hybrid / remote).
- **`server.py`** serves the board, a market-overview analytics page, and a 1080×1080
  LinkedIn-visual composer.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# scrape one city (free sources only)
python3 main.py --template berlin --free-only

# run the board
uvicorn server:app --host 0.0.0.0 --port 8642
```

Open http://localhost:8642 — pick a city from the heading dropdown.

Each city is a template in `templates/<city>.json` (search terms, title filters,
seniority rules) and writes to its own SQLite DB (`jobs_<city>.db`). Add a city by
copying a template and registering it in `profiles/__init__.py`.

## Sources & credits

This project stands on open-source work and free public APIs. Thanks to:

- **[JobSpy](https://github.com/Bunsly/JobSpy)** — LinkedIn & Indeed guest-API scraping (`python-jobspy`)
- **[Careerjet](https://www.careerjet.com/)** public search API
- **[EURES](https://eures.europa.eu/)** — European Commission job mobility API
- **[Arbeitnow](https://www.arbeitnow.com/)** and the **[Bundesagentur für Arbeit](https://www.arbeitsagentur.de/)** job APIs
- **[Greenhouse](https://developers.greenhouse.io/job-board.html)** public job-board JSON API
- ATS boards: **SmartRecruiters, Ashby, Personio, Lever, Workable** public endpoints
- **[Getro](https://www.getro.com/)** VC-portfolio board search API
- RSS / WordPress feeds: GermanTechJobs, BerlinStartupJobs, FoundHamburg, a16z Speedrun
- **[JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)** (optional, metered free tier)

Built with **[FastAPI](https://fastapi.tiangolo.com/)**, **[Uvicorn](https://www.uvicorn.org/)**,
**[Jinja2](https://palletsprojects.com/p/jinja/)**, **[RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)**
and **[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/)**. The composer uses
**[html-to-image](https://github.com/bubkoo/html-to-image)**, **[jsPDF](https://github.com/parallax/jsPDF)**
and **[JSZip](https://github.com/Stuk/jszip)**.

Please respect each source's terms of use. This board links back to every original posting.

---

Built by Ahmed Sulaiman.
