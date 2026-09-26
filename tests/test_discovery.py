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


def test_ashby_compensation_is_added_to_description():
    data = {"jobs": [{"id": "1", "title": "AI Engineer", "location": "NYC", "jobUrl": "u", "applyUrl": "a",
                      "descriptionPlain": "Build agents.",
                      "compensation": {"compensationTierSummary": "$150K – $200K • Offers Equity"}}]}
    job = ashby.parse_jobs(data, "x", "X")[0]
    assert job.description.endswith("Compensation: $150K – $200K • Offers Equity")


def test_job_from_url_greenhouse_and_ashby():
    import pytest

    from jobagent.discovery import job_from_url

    client = mock_http()
    gh = job_from_url(client, "https://job-boards.greenhouse.io/acme/jobs/5001?gh_src=abc")
    assert (gh.ats, gh.company, gh.job_id) == ("greenhouse", "acme", "5001")
    gh2 = job_from_url(client, "https://boards.greenhouse.io/embed/job_app?for=acme&token=5001")
    assert gh2.job_id == "5001"
    ab = job_from_url(client, "https://jobs.ashbyhq.com/ramp/0b1c2d3e-aaaa-bbbb-cccc-000000000001/application")
    assert (ab.ats, ab.company) == ("ashby", "ramp")
    with pytest.raises(ValueError):
        job_from_url(client, "https://example.com/careers/123")
    with pytest.raises(ValueError):
        job_from_url(client, "https://job-boards.greenhouse.io/acme/jobs/999")
