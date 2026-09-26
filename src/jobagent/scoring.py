"""Cheap rule filter, then an LLM fit score."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from .config import Preferences
from .llm import LLM, untrusted
from .models import Job, MasterResume


def rule_filter(job: Job, prefs: Preferences, now: datetime | None = None) -> str | None:
    """Return a reason string if the job should be filtered out, else None."""
    title = job.title.lower()
    if prefs.title_include and not any(k.lower() in title for k in prefs.title_include):
        return "title does not match title_include"
    for k in prefs.title_exclude:
        if k.lower() in title:
            return f"title contains excluded keyword {k!r}"
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


def score_job(llm: LLM, job: Job, master: MasterResume, max_jd_chars: int = 7000) -> ScoreOutput:
    profile = "\n".join(
        [f"{r.title} at {r.company} ({r.start} - {r.end})" for r in master.roles]
        + [f"- {b.text}" for b in master.iter_bullets()]
        + ["Skills: " + ", ".join(master.all_skills())]
        + [f"Education: {e.degree}, {e.school}" for e in master.education]
    )
    user = (
        f"CANDIDATE:\n{profile}\n\n"
        f"JOB: {job.title} at {job.company_name} ({job.location})\n"
        + untrusted("job_description", job.description[:max_jd_chars])
    )
    return llm.json("fast", SYSTEM, user, ScoreOutput)
