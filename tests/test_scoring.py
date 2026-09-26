from conftest import FakeLLM

from jobagent.config import Preferences
from jobagent.models import Job
from jobagent.scoring import looks_us, rule_filter, score_job


def job(title="Software Engineer", location="San Francisco, CA", employment_type=None):
    return Job(ats="ashby", company="a", company_name="A", job_id="1", title=title, location=location,
               url="u", apply_url="u", employment_type=employment_type)


PREFS = Preferences(
    title_include=["software engineer", "ai engineer", "forward deployed"],
    title_exclude=["senior", "sr.", "intern", "lead"],
    us_only=True, employment_types=["FullTime"], max_age_days=None,
)


def test_titles_match_whole_words_only():
    assert rule_filter(job("Software Engineer, Internal Tools"), PREFS) is None  # "intern" != "Internal"
    assert rule_filter(job("Software Engineer Intern"), PREFS)
    assert rule_filter(job("Sr. Software Engineer"), PREFS)
    assert rule_filter(job("Forward Deployed Engineer"), PREFS) is None
    assert rule_filter(job("Product Designer"), PREFS)


def test_us_only_and_full_time():
    assert rule_filter(job(location="London, UK"), PREFS)
    assert rule_filter(job(location="Remote - US"), PREFS) is None
    assert rule_filter(job(employment_type="Contract"), PREFS)
    assert rule_filter(job(employment_type="FullTime"), PREFS) is None
    assert rule_filter(job(employment_type=None), PREFS) is None  # Greenhouse doesn't say


def test_looks_us():
    assert looks_us("Austin, TX") and looks_us("New York City") and looks_us("")
    assert not looks_us("Toronto, ON") and not looks_us("Bangalore, India")


def test_candidate_notes_reach_the_scorer():
    from pathlib import Path

    from jobagent.config import load_master

    llm = FakeLLM()
    seen = {}
    orig = llm.json

    def spy(tier, system, user, schema):
        seen["user"] = user
        return orig(tier, system, user, schema)

    llm.json = spy
    m = load_master(Path(__file__).resolve().parents[1] / "profile.example/master_resume.yaml")
    score_job(llm, job(), m, notes="Needs H-1B sponsorship; no-sponsorship roles are dealbreakers.")
    assert "Needs H-1B sponsorship" in seen["user"]
