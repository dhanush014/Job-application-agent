from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
import yaml

from jobagent.answers import ChoiceOutput, CoverLetter, WrittenOutput
from jobagent.config import load_config
from jobagent.models import TailorOutput
from jobagent.scoring import ScoreOutput
from jobagent.store.sqlite import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIX / name).read_text())


class FakeLLM:
    """Deterministic stand-in for Groq, keyed on the requested schema."""

    def __init__(self, score: int = 88, rewrites: dict[str, str] | None = None):
        self.score = score
        self.rewrites = rewrites or {}
        self.calls: list[str] = []

    def json(self, tier, system, user, schema):
        self.calls.append(schema.__name__)
        if schema is ScoreOutput:
            return ScoreOutput(score=self.score, summary="Strong backend match.", matched=["Go", "Kafka"],
                               gaps=[], dealbreakers=[], jd_keywords=["Go", "Kafka", "PostgreSQL", "Kubernetes"])
        if schema is TailorOutput:
            ids = [l.split("]")[0][1:] for l in user.splitlines() if l.startswith("[")]
            return TailorOutput.model_validate({
                "bullets": [{"source_id": i, "text": self.rewrites.get(i, ""), "relevance": 100 - n}
                            for n, i in enumerate(ids)],
                "skills": ["Kafka", "Go", "Kubernetes", "NotASkill"],
            })
        if schema is ChoiceOutput:
            fields = [l.split("field=")[1].split(":")[0] for l in user.splitlines() if "field=" in l]
            return ChoiceOutput.model_validate({"answers": [{"field": f, "choices": ["Go"], "confidence": 90} for f in fields]})
        if schema is WrittenOutput:
            fields = [l.split("field=")[1].split(" ")[0] for l in user.splitlines() if "field=" in l]
            return WrittenOutput.model_validate({"answers": [
                {"field": f, "text": "I have spent three years building payment systems and want to keep doing it at Acme.", "confidence": 85}
                for f in fields]})
        if schema is CoverLetter:
            return CoverLetter(text="Dear Hiring Team,\n\n" + "I build reliable payment systems. " * 12 + "\n\nAlex Rivera")
        raise AssertionError(f"unexpected schema {schema}")


def mock_http() -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "boards-api.greenhouse.io" in url and url.split("?")[0].endswith("/jobs"):
            return httpx.Response(200, json=fixture("greenhouse_jobs.json"))
        if "boards-api.greenhouse.io" in url and "/jobs/" in url:
            return httpx.Response(200, json=fixture("greenhouse_questions.json"))
        if "api.ashbyhq.com" in url:
            return httpx.Response(200, json=fixture("ashby_jobs.json"))
        if "non-user-graphql" in url:
            return httpx.Response(200, json=fixture("ashby_form.json"))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def project(tmp_path: Path):
    shutil.copytree(ROOT / "profile.example", tmp_path / "profile")
    (tmp_path / "companies.yaml").write_text(yaml.safe_dump({"companies": [
        {"ats": "greenhouse", "slug": "acme", "name": "Acme"},
        {"ats": "ashby", "slug": "ramp", "name": "Ramp"},
    ]}))
    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    cfg["preferences"]["max_age_days"] = None
    cfg["apply"]["delay_seconds"] = [0, 0]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


@pytest.fixture
def cfg(project):
    return load_config(project / "config.yaml")


@pytest.fixture
def store(cfg):
    return SQLiteStore(cfg.path(cfg.storage.sqlite_path))
