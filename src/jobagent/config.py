"""Configuration: config.yaml + profile files + secrets from the environment / .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .models import MasterResume


class Preferences(BaseModel):
    title_include: list[str] = Field(default_factory=list)  # any keyword must appear in the title
    title_exclude: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)  # substrings; empty = anywhere
    allow_remote: bool = True
    max_age_days: int | None = 30
    min_score: int = 70


class LLMConfig(BaseModel):
    # Groq model ids change over time; check https://console.groq.com/docs/models
    fast_model: str = "llama-3.1-8b-instant"  # scoring, option picking
    smart_model: str = "llama-3.3-70b-versatile"  # tailoring, written answers
    temperature: float = 0.2
    max_jd_chars: int = 7000


class ApplyConfig(BaseModel):
    auto_submit: bool = True  # submit READY applications without asking
    dry_run: bool = True  # fill forms but never click submit (turn off once you trust it)
    daily_limit: int = 50
    max_prepare_per_run: int = 120  # bounds Groq usage per run
    delay_seconds: tuple[int, int] = (15, 60)
    headless: bool = True
    review_headless: bool = False  # one-click from the dashboard opens a visible browser
    allow_reapply: bool = True
    reapply_after_days: int = 30
    min_answer_confidence: int = 70
    # when_asked: write + attach a cover letter whenever the form has a cover letter field
    # when_required: only when that field is required; never: only if required (can't skip those)
    cover_letter: Literal["when_asked", "when_required", "never"] = "when_asked"
    browser_executable: str | None = None
    manual_finish_seconds: int = 600  # headed mode: time you get to fix + submit by hand


class StorageConfig(BaseModel):
    backend: Literal["sqlite", "supabase"] = "sqlite"
    sqlite_path: str = "data/jobagent.db"
    supabase_bucket: str = "applications"
    excel_path: str | None = "data/applications.xlsx"  # refreshed after every run


class EmailConfig(BaseModel):
    """IMAP access for Greenhouse's emailed security codes (optional)."""

    imap_host: str | None = None  # e.g. imap.gmail.com
    username: str | None = None
    # password comes from IMAP_PASSWORD (for Gmail: an app password)


class Config(BaseModel):
    data_dir: str = "data"
    profile_dir: str = "profile"
    companies_file: str = "companies.yaml"
    preferences: Preferences = Field(default_factory=Preferences)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)

    root: Path = Path(".")

    def path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.root / q

    @property
    def data_path(self) -> Path:
        d = self.path(self.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def master_resume(self) -> MasterResume:
        return load_master(self.path(self.profile_dir) / "master_resume.yaml")

    def answer_bank(self) -> dict:
        p = self.path(self.profile_dir) / "answers.yaml"
        return yaml.safe_load(p.read_text()) or {}

    def companies(self) -> list["Company"]:
        data = yaml.safe_load(self.path(self.companies_file).read_text()) or {}
        return [Company.model_validate(c) for c in data.get("companies", [])]


class Company(BaseModel):
    ats: Literal["greenhouse", "ashby"]
    slug: str
    name: str | None = None


def load_master(path: Path) -> MasterResume:
    return MasterResume.model_validate(yaml.safe_load(Path(path).read_text()))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    root = path.parent.resolve()
    load_dotenv(root / ".env")
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    cfg = Config.model_validate(raw or {})
    cfg.root = root
    return cfg
