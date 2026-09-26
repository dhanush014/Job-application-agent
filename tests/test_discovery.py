from conftest import fixture, mock_http

from jobagent.config import Company
from jobagent.discovery import ashby, fetch_all, greenhouse
from jobagent.models import FieldType


def test_greenhouse_jobs_parsed():
    jobs = greenhouse.parse_jobs(fixture("greenhouse_jobs.json"), "acme", "Acme")
    assert [j.job_id for j in jobs] == ["5001", "5002"]
    j = jobs[0]
    assert j.apply_url == "https://job-boards.greenhouse.io/acme/jobs/5001"
    assert "payments infrastructure" in j.description and "<" not in j.description
    assert "- Go, Kafka, PostgreSQL" in j.description


def test_greenhouse_form_parsed():
    fields = {f.name: f for f in greenhouse.parse_form(fixture("greenhouse_questions.json"))}
    assert fields["resume"].type == FieldType.FILE and fields["resume"].required
    assert "resume_text" not in fields and "question_1007" not in fields
    assert fields["question_1002"].type == FieldType.SELECT
    assert [o.label for o in fields["question_1002"].options] == ["Yes", "No"]
    assert fields["question_1005"].type == FieldType.MULTISELECT
    assert fields["gender"].eeo


def test_ashby_jobs_and_form_parsed():
    jobs = ashby.parse_jobs(fixture("ashby_jobs.json"), "ramp", "Ramp")
    assert len(jobs) == 1 and jobs[0].remote and jobs[0].apply_url.endswith("/application")
    fields = {f.name: f for f in ashby.parse_form(fixture("ashby_form.json"))}
    assert fields["_systemfield_resume"].type == FieldType.FILE
    assert fields["q_sponsor"].type == FieldType.BOOLEAN
    assert fields["q_level"].options[1].value == "growth"


def test_fetch_all_survives_broken_board():
    cs = [Company(ats="greenhouse", slug="acme"), Company(ats="greenhouse", slug="acme")]
    client = mock_http()
    assert len(fetch_all(client, cs)) == 4
