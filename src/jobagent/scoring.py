"""Cheap rule filter, then an LLM fit score."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from .config import Preferences
from .llm import LLM, untrusted
from .models import Job, MasterResume


US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
]
US_CITIES = [
    "san francisco", "bay area", "new york", "nyc", "seattle", "boston", "austin", "chicago",
    "los angeles", "denver", "atlanta", "pittsburgh", "palo alto", "mountain view", "menlo park",
    "sunnyvale", "san jose", "san mateo", "redwood city", "cambridge, ma", "philadelphia",
    "washington, d.c", "miami", "dallas", "houston", "san diego", "portland", "salt lake",
]


def looks_us(location: str) -> bool:
    """Best-effort: is this posting in (or open to) the US? Unknown -> True."""
    loc = location.strip()
    if not loc or loc.lower() in ("remote", "anywhere", "hybrid"):
        return True
    low = loc.lower()
    if re.search(r"\b(united states|usa|u\.s\.a?\.?|us|america)\b", low):
        return True
    if any(re.search(rf"\b{re.escape(n)}\b", low) for n in US_STATE_NAMES + US_CITIES):
        return True
    return any(code in US_STATES for code in re.findall(r",\s*([A-Z]{2})\b", loc))


def _has(title: str, keyword: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(keyword.lower())}(?![\w])", title) is not None


def rule_filter(job: Job, prefs: Preferences, now: datetime | None = None) -> str | None:
    """Return a reason string if the job should be filtered out, else None."""
    title = job.title.lower()
    if prefs.title_include and not any(_has(title, k) for k in prefs.title_include):
        return "title does not match title_include"
    for k in prefs.title_exclude:
        if _has(title, k):
            return f"title contains excluded keyword {k!r}"
    if prefs.employment_types and job.employment_type and job.employment_type not in prefs.employment_types:
        return f"employment type {job.employment_type!r} not in {prefs.employment_types}"
    if prefs.us_only and not looks_us(job.location):
        return f"location {job.location!r} is outside the US"
    if prefs.locations:
        loc = job.location.lower()
        remote_ok = prefs.allow_remote and (job.remote or "remote" in loc)
        if not remote_ok and not any(l.lower() in loc for l in prefs.locations):
            return f"location {job.location!r} not in preferences"
    if prefs.max_age_days and job.posted_at:
        try:
            posted = datetime.fromisoformat(job.posted_at.replace("Z", "+00:00"))
            now = now or datetime.now(timezone.utc)
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            if now - posted > timedelta(days=prefs.max_age_days):
                return f"posted more than {prefs.max_age_days} days ago"
        except ValueError:
            pass
    return None


class ScoreOutput(BaseModel):
    score: int = Field(ge=0, le=100)
    summary: str = Field(max_length=600)
    matched: list[str] = Field(default_factory=list, max_length=15)
    gaps: list[str] = Field(default_factory=list, max_length=15)
    dealbreakers: list[str] = Field(default_factory=list, max_length=10)
    jd_keywords: list[str] = Field(default_factory=list, max_length=40)


SYSTEM = """You are a strict technical recruiter scoring candidate/job fit.
Score 0-100: 85+ strong match, 70-84 good, 50-69 stretch, <50 poor.
Consider seniority, core skills, domain and hard requirements (years, degrees, clearances, location).
dealbreakers: hard requirements the candidate clearly does not meet (empty if none).
jd_keywords: the most important skills/tools/technologies named in the job description, verbatim."""


def score_job(llm: LLM, job: Job, master: MasterResume, max_jd_chars: int = 7000, notes: str = "") -> ScoreOutput:
    profile = "\n".join(
        [f"{r.title} at {r.company} ({r.start} - {r.end})" for r in master.roles + master.academic]
        + [f"- {b.text}" for b in master.iter_bullets()]
        + ["Skills: " + ", ".join(master.all_skills())]
        + [f"Education: {e.degree}, {e.school}" for e in master.education]
    )
    if notes.strip():
        profile += f"\n\nCANDIDATE NOTES (preferences and constraints; apply them strictly):\n{notes.strip()}"
    user = (
        f"CANDIDATE:\n{profile}\n\n"
        f"JOB: {job.title} at {job.company_name} ({job.location})\n"
        + untrusted("job_description", job.description[:max_jd_chars])
    )
    return llm.json("fast", SYSTEM, user, ScoreOutput)
