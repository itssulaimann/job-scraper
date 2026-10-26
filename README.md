# ProductLab Jobs

A tiny public board of fresh **Berlin product jobs** — browse, pick a few, and turn them into a branded LinkedIn post.

- **Browse** de-duplicated roles, auto-refreshed daily from free public sources.
- **Compose** a LinkedIn carousel + summary card from 3–10 selected jobs (rendered and exported in the browser as PDF/PNG).
- **Analytics** with a company-name auditor that flags the same employer scraped under slightly different names.

FastAPI + vanilla JS. Jobs are scraped from free sources, de-duplicated, and tidied with a short Claude QA pass.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# add a search template under templates/ (see templates/example.json), then:
uvicorn server:app --port 8642   # → http://localhost:8642
```

## Credits

This stands on a lot of open-source work.

**Libraries**
- [FastAPI](https://github.com/fastapi/fastapi) · [Uvicorn](https://github.com/encode/uvicorn) · [Jinja](https://github.com/pallets/jinja)
- [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) · [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)
- [html-to-image](https://github.com/bubkoo/html-to-image) · [jsPDF](https://github.com/parallax/jsPDF) · [JSZip](https://github.com/Stuk/jszip)

**Job sources & scrapers**
- [JobSpy](https://github.com/speedyapply/JobSpy) — LinkedIn & Indeed
- [Arbeitnow](https://www.arbeitnow.com/api) · [Bundesagentur für Arbeit](https://jobsuche.api.bund.dev/) · [EURES](https://eures.europa.eu/) (EU Commission)
- [Greenhouse](https://developers.greenhouse.io/job-board.html) · [SmartRecruiters](https://developers.smartrecruiters.com/) · [Ashby](https://developers.ashbyhq.com/) · [Personio](https://developer.personio.de/) · [Lever](https://github.com/lever/postings-api) · [Workable](https://workable.readme.io/)
- [Careerjet](https://www.careerjet.com/partners/api/) · [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
- GermanTechJobs & BerlinStartupJobs (RSS) · [a16z Speedrun](https://a16z.com/speedrun/) · [Getro](https://getro.com/) VC boards · [foundhamburg](https://foundhamburg.com/jobs)
- [ScrapeGraphAI](https://github.com/ScrapeGraphAI/Scrapegraph-ai) + [Scrapling](https://github.com/D4Vinci/Scrapling) for career-page extraction

Listing clean-up (title tidy + duplicate/mismatch flagging) by [Claude](https://claude.com).
