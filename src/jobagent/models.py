"""Strict data schemas shared by every stage of the pipeline.

The master resume is the single source of truth for facts. The LLM only ever
produces `TailorOutput` (a ranked, lightly rephrased selection of master
bullets); layout is decided by code in `jobagent.resume.render`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# One bullet may wrap to at most two lines in the template; 200 chars is the
# hard ceiling, the fitter decides what actually fits on the page.
MAX_BULLET_CHARS = 200


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------
# Master resume (written once by the user)
# --------------------------------------------------------------------------


class Link(Strict):
    label: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=1)


class Contact(Strict):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str = Field(min_length=5, max_length=30)
    location: str = Field(min_length=1, max_length=60)
    links: list[Link] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _one_line(self) -> "Contact":
        parts = [self.location, self.phone, self.email] + [l.label for l in self.links]
        if sum(len(p) for p in parts) + 3 * (len(parts) - 1) > 125:
            raise ValueError("contact line too long for one line; shorten link labels")
        return self


class Education(Strict):
    school: str = Field(min_length=1, max_length=80)
    degree: str = Field(min_length=1, max_length=100)
    location: str = Field(default="", max_length=60)
    start: str = Field(default="", max_length=20)
    end: str = Field(min_length=1, max_length=20)
    details: list[str] = Field(default_factory=list, max_length=2)


class Bullet(Strict):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    text: str = Field(min_length=10, max_length=MAX_BULLET_CHARS)
    skills: list[str] = Field(default_factory=list)


class Role(Strict):
    id: str = Field(min_length=1, max_length=40)
    company: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=80)
    location: str = Field(default="", max_length=60)
    start: str = Field(min_length=1, max_length=20)
    end: str = Field(min_length=1, max_length=20)
    min_bullets: int = Field(default=2, ge=0, le=6)
    max_bullets: int | None = Field(default=None, ge=1, le=12)  # cap per role (None = as many as fit)
    bullets: list[Bullet] = Field(min_length=1)

    @model_validator(mode="after")
    def _min_le_total(self) -> "Role":
        if self.min_bullets > len(self.bullets):
            raise ValueError(f"role {self.id}: min_bullets > number of bullets")
        if self.max_bullets is not None and self.max_bullets < self.min_bullets:
            raise ValueError(f"role {self.id}: max_bullets < min_bullets")
        return self


class Project(Strict):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="", max_length=80)  # e.g. "AI Consultant (Capstone)"; shown under the name
    link: str = ""  # shown under the name when there is no role
    location: str = Field(default="", max_length=60)
    dates: str = Field(default="", max_length=40)  # e.g. "Jun 2026 – Aug 2026"
    min_bullets: int = Field(default=0, ge=0, le=4)
    max_bullets: int | None = Field(default=None, ge=1, le=8)
    bullets: list[Bullet] = Field(min_length=1)


class MasterResume(Strict):
    contact: Contact
    education: list[Education] = Field(min_length=1, max_length=3)
    roles: list[Role] = Field(min_length=1, max_length=8)
    projects: list[Project] = Field(default_factory=list, max_length=6)
    skills: dict[str, list[str]] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def _unique_ids(self) -> "MasterResume":
        seen: set[str] = set()
        for b in self.iter_bullets():
            if b.id in seen:
                raise ValueError(f"duplicate bullet id: {b.id}")
            seen.add(b.id)
        return self

    def iter_bullets(self):
        for r in self.roles:
            yield from r.bullets
        for p in self.projects:
            yield from p.bullets

    def bullet_index(self) -> dict[str, Bullet]:
        return {b.id: b for b in self.iter_bullets()}

    def owner_of(self) -> dict[str, str]:
        """bullet id -> role/project id"""
        out = {}
        for r in self.roles:
            for b in r.bullets:
                out[b.id] = r.id
        for p in self.projects:
            for b in p.bullets:
                out[b.id] = p.id
        return out

    def all_skills(self) -> list[str]:
        return [s for group in self.skills.values() for s in group]

    def all_text(self) -> str:
        parts = [b.text for b in self.iter_bullets()]
        parts += self.all_skills()
        parts += [s for b in self.iter_bullets() for s in b.skills]
        parts += [r.title for r in self.roles] + [p.name for p in self.projects]
        return "\n".join(parts)


# --------------------------------------------------------------------------
# LLM output for tailoring (the only thing the LLM controls)
# --------------------------------------------------------------------------


class TailoredBullet(Strict):
    source_id: str
    text: str = Field(max_length=MAX_BULLET_CHARS)
    relevance: int = Field(ge=0, le=100)


class TailorOutput(Strict):
    bullets: list[TailoredBullet]
    skills: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# What actually gets rendered (built by the fitter, never by the LLM)
# --------------------------------------------------------------------------


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no whitespace stripping: runs keep their spaces
    t: str
    b: bool = False  # bold


class DocEntry(Strict):
    heading: str
    subheading: str = ""
    location: str = ""
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)
    # same bullets split into plain/bold runs (skills in bold); empty = all plain
    segments: list[list[Segment]] = Field(default_factory=list)
    ids: list[str] = Field(default_factory=list)  # master bullet id per bullet


class SkillLine(Strict):
    category: str
    items: str


class Layout(Strict):
    font: float = Field(default=1 / 3, ge=0, le=1)  # 0 = 9.5pt, 1/3 = 10pt, 1 = 11pt
    spacing: float = Field(default=0.0, ge=0, le=1)  # 0 = compact, 1 = airy gaps between items/sections
    stretch: bool = False  # spread any last leftover space between sections


class ResumeDoc(Strict):
    contact: Contact
    education: list[Education]
    roles: list[DocEntry]
    projects: list[DocEntry] = Field(default_factory=list)
    skills: list[SkillLine]
    layout: Layout = Field(default_factory=Layout)


# --------------------------------------------------------------------------
# Jobs, forms, answers, applications
# --------------------------------------------------------------------------

ATS = Literal["greenhouse", "ashby"]


class Job(BaseModel):
    ats: ATS
    company: str  # board slug
    company_name: str
    job_id: str
    title: str
    location: str = ""
    url: str
    apply_url: str
    description: str = ""
    posted_at: str | None = None
    remote: bool | None = None

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.company}:{self.job_id}"


class FieldType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    MULTISELECT = "multiselect"
    BOOLEAN = "boolean"
    FILE = "file"
    UNKNOWN = "unknown"


class Option(BaseModel):
    label: str
    value: str


class FormField(BaseModel):
    name: str  # DOM id / field path used by the filler
    label: str
    type: FieldType
    required: bool = False
    options: list[Option] = Field(default_factory=list)
    description: str = ""
    eeo: bool = False


AnswerSource = Literal["profile", "bank", "llm", "llm_choice", "file", "user", "none"]


class Answer(BaseModel):
    field: str
    label: str
    type: FieldType
    required: bool = False
    options: list[Option] = Field(default_factory=list)
    value: str | list[str] | None = None
    source: AnswerSource = "none"
    confidence: int = 0  # 0-100
    sensitive: bool = False
    note: str = ""


class Status(str, Enum):
    FILTERED = "filtered"  # failed cheap rule filter (title/location/age)
    LOW_FIT = "low_fit"  # LLM fit score under threshold
    NEEDS_REVIEW = "needs_review"  # fully prepared, a gate wants a human look
    READY = "ready"  # fully prepared, passes every gate, will auto-submit
    DRY_RUN = "dry_run"  # form filled successfully but submit was not clicked
    QUEUED = "queued"  # you clicked Apply on the hosted dashboard; the worker will submit it
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    DISMISSED = "dismissed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Application(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    job_key: str
    ats: ATS
    company: str
    company_name: str
    job_id: str
    title: str
    location: str = ""
    url: str
    apply_url: str
    jd: str = ""
    status: Status = Status.FILTERED
    score: int | None = None
    fit_summary: str = ""
    matched: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    jd_keywords: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    resume_json: dict | None = None
    resume_pdf: str | None = None
    cover_letter: str | None = None
    cover_letter_pdf: str | None = None
    answers: list[Answer] = Field(default_factory=list)
    screenshot: str | None = None
    error: str | None = None
    attempt: int = 1
    # work the hosted dashboard asked the worker to do ("prepare" / "reapply")
    request: Literal["prepare", "reapply"] | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    applied_at: datetime | None = None

    @classmethod
    def from_job(cls, job: Job, attempt: int = 1) -> "Application":
        return cls(
            job_key=job.key,
            ats=job.ats,
            company=job.company,
            company_name=job.company_name,
            job_id=job.job_id,
            title=job.title,
            location=job.location,
            url=job.url,
            apply_url=job.apply_url,
            jd=job.description,
            attempt=attempt,
        )

    def to_job(self) -> Job:
        return Job(
            ats=self.ats,
            company=self.company,
            company_name=self.company_name,
            job_id=self.job_id,
            title=self.title,
            location=self.location,
            url=self.url,
            apply_url=self.apply_url,
            description=self.jd,
        )

    @field_validator("answers", mode="before")
    @classmethod
    def _none_answers(cls, v):
        return v or []
