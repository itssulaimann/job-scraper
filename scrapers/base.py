from dataclasses import dataclass
from typing import Optional


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str
    seniority: str = "Senior PM"
    posted_date: Optional[str] = None
    description: str = ""
