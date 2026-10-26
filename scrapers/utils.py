"""Shared utilities for scrapers."""
import re
from datetime import date, timedelta


def parse_relative_date(posted_at: str) -> str | None:
    """Convert relative date strings like '3 days ago' to ISO date."""
    if not posted_at:
        return None
    s = posted_at.lower()
    today = date.today()
    if any(w in s for w in ("today", "just posted", "just now", "hour", "minute")):
        return today.isoformat()
    m = re.search(r'(\d+)\+?\s+day', s)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r'(\d+)\s+week', s)
    if m:
        return (today - timedelta(weeks=int(m.group(1)))).isoformat()
    if "month" in s:
        return (today - timedelta(days=30)).isoformat()
    # Try parsing directly as an ISO date prefix
    try:
        date.fromisoformat(posted_at[:10])
        return posted_at[:10]
    except Exception:
        pass
    return None
