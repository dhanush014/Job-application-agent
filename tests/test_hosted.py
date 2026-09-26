"""Hosted (Vercel) dashboard + worker queue."""

import base64
import runpy
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, FakeLLM, mock_http
from fastapi.testclient import TestClient

from jobagent.config import load_config
from jobagent.models import Status
from jobagent.pipeline import Pipeline
from jobagent.web.app import create_app

AUTH = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}


@pytest.fixture
def prepared(cfg, store):
    from test_pipeline import FakeSubmitter

    sub = FakeSubmitter()
    worker = Pipeline(cfg, store, FakeLLM(), http=mock_http(), submitter=sub)
    worker.run(submit=False)
    dashboard = Pipeline(cfg, store, llm=None, http=mock_http())  # hosted side never calls the LLM
    return worker, dashboard, sub


def test_hosted_requires_password(prepared, monkeypatch):
    _, dashboard, _ = prepared
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    assert TestClient(create_app(dashboard, hosted=True)).get("/").status_code == 503
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    client = TestClient(create_app(dashboard, hosted=True))
    assert client.get("/").status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"me:wrong").decode()}
    assert client.get("/", headers=bad).status_code == 401
    assert client.get("/", headers=AUTH).status_code == 200


def test_hosted_apply_is_queued_and_worker_submits(prepared, store, monkeypatch):
    worker, dashboard, sub = prepared
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    client = TestClient(create_app(dashboard, hosted=True))
    a = next(x for x in store.list([Status.READY]))
    assert "Run now" not in client.get("/", headers=AUTH).text
    client.post(f"/a/{a.id}/apply", headers=AUTH, data={})
    assert store.get(a.id).status == Status.QUEUED
    assert not sub.calls  # nothing ran on the dashboard side
    stats = worker.process_queue()
    assert stats.submitted == 1
    assert store.get(a.id).status == Status.APPLIED
    assert sub.calls[-1][2] is False  # a real submit even if the config is in dry-run


def test_hosted_prepare_request_handled_by_worker(prepared, store, monkeypatch):
    worker, dashboard, _ = prepared
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    client = TestClient(create_app(dashboard, hosted=True))
    low = next(a for a in store.list([Status.FILTERED]))
    client.post(f"/a/{low.id}/prepare", headers=AUTH)
    assert store.get(low.id).request == "prepare"
    worker.process_queue()
    done = store.get(low.id)
    assert done.request is None and done.status in (Status.READY, Status.NEEDS_REVIEW)
    assert done.resume_pdf


def test_mark_applied_by_hand(prepared, store, monkeypatch):
    _, dashboard, _ = prepared
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    client = TestClient(create_app(dashboard, hosted=True))
    a = next(x for x in store.list([Status.READY]))
    client.post(f"/a/{a.id}/mark-applied", headers=AUTH)
    b = store.get(a.id)
    assert b.status == Status.APPLIED and b.applied_at


def test_pdfs_served_from_remote_storage_when_not_on_disk(prepared, store, monkeypatch):
    _, dashboard, _ = prepared
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    a = next(x for x in store.list([Status.READY]))
    Path(a.resume_pdf).unlink()  # serverless: the file isn't here
    monkeypatch.setattr(store, "download", lambda key: b"%PDF-remote" if key == f"{a.id}/resume.pdf" else None)
    r = TestClient(create_app(dashboard, hosted=True)).get(f"/a/{a.id}/resume.pdf", headers=AUTH)
    assert r.status_code == 200 and r.content == b"%PDF-remote"


def test_worker_downloads_missing_pdf_before_submitting(prepared, store, monkeypatch):
    worker, _, sub = prepared
    a = next(x for x in store.list([Status.READY]))
    real = Path(a.resume_pdf).read_bytes()
    Path(a.resume_pdf).unlink()
    monkeypatch.setattr(store, "download", lambda key: real if key.endswith("resume.pdf") else None)
    a.status = Status.QUEUED
    store.save(a)
    assert worker.process_queue().submitted == 1  # FakeSubmitter asserts the file exists


def test_config_and_profile_from_env(project, monkeypatch):
    monkeypatch.setenv("JOBAGENT_CONFIG", yaml.safe_dump({"apply": {"daily_limit": 7}}))
    monkeypatch.setenv("JOBAGENT_MASTER_RESUME", (ROOT / "profile.example/master_resume.yaml").read_text())
    monkeypatch.setenv("JOBAGENT_COMPANIES", "companies: [{ats: ashby, slug: x}]")
    monkeypatch.setenv("JOBAGENT_DATA_DIR", str(project / "tmpdata"))
    (project / "profile" / "master_resume.yaml").unlink()  # prove it isn't read
    cfg = load_config(project / "nope.yaml")
    assert cfg.apply.daily_limit == 7
    assert cfg.master_resume().contact.name == "Alex Rivera"
    assert cfg.companies()[0].slug == "x"
    assert cfg.data_path == project / "tmpdata"


def test_vercel_entry_point(monkeypatch, tmp_path):
    import jobagent.store as store_mod
    from jobagent.store.sqlite import SQLiteStore

    for k in ["DASHBOARD_PASSWORD", "SUPABASE_URL", "SUPABASE_KEY", "JOBAGENT_CONFIG", "JOBAGENT_MASTER_RESUME"]:
        monkeypatch.delenv(k, raising=False)
    app = runpy.run_path(str(ROOT / "api/index.py"))["app"]
    r = TestClient(app).get("/")
    assert r.status_code == 500 and "DASHBOARD_PASSWORD" in r.text  # tells you what to set

    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setenv("JOBAGENT_CONFIG", "storage: {backend: supabase}")
    monkeypatch.setenv("JOBAGENT_MASTER_RESUME", (ROOT / "profile.example/master_resume.yaml").read_text())
    monkeypatch.setenv("JOBAGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store_mod, "open_store", lambda cfg: SQLiteStore(tmp_path / "db.sqlite"))
    app = runpy.run_path(str(ROOT / "api/index.py"))["app"]
    assert TestClient(app).get("/", headers=AUTH).status_code == 200
