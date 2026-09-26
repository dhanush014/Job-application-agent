from pathlib import Path

import yaml
from conftest import FakeLLM, fixture

from jobagent.answers import Bank, answer_form, pick_option, review_reasons
from jobagent.config import load_master
from jobagent.discovery import ashby, greenhouse
from jobagent.models import FieldType, FormField, Option

ROOT = Path(__file__).resolve().parents[1]
M = load_master(ROOT / "profile.example/master_resume.yaml")
BANK = Bank.from_yaml(yaml.safe_load((ROOT / "profile.example/answers.yaml").read_text()))


def run(fields, llm=None):
    return answer_form(fields, BANK, M, llm or FakeLLM(), "Senior SWE", "Acme", "We use Go.")


def test_greenhouse_form_answers():
    answers, needs_cover = run(greenhouse.parse_form(fixture("greenhouse_questions.json")))
    by = {a.field: a for a in answers}
    assert by["first_name"].value == "Alex" and by["first_name"].source == "profile"
    assert by["resume"].value == "resume"
    assert by["cover_letter"].value == "cover_letter" and needs_cover  # optional, but asked for -> attach
    assert by["question_1001"].value == "https://linkedin.com/in/alexrivera"
    assert by["question_1002"].value == "Yes" and by["question_1002"].source == "bank"
    assert by["question_1003"].value == "No"
    assert by["question_1004"].source == "llm" and "payment" in by["question_1004"].value
    assert by["question_1005"].value == ["Go"]
    assert by["gender"].value == "Decline To Self Identify"
    assert by["question_1006"].value == "No"  # "security clearance" is in the bank


def test_sensitive_question_without_bank_answer_goes_to_review():
    f = FormField(name="q9", label="Have you ever been convicted of a felony?", type=FieldType.SELECT,
                  required=True, options=[Option(label="Yes", value="1"), Option(label="No", value="0")])
    llm = FakeLLM()
    answers, _ = run([f], llm)
    assert answers[0].value is None and answers[0].sensitive
    assert "ChoiceOutput" not in llm.calls  # never asked the LLM
    assert review_reasons(answers, 70)


def test_ashby_boolean_and_name():
    answers, _ = run(ashby.parse_form(fixture("ashby_form.json")))
    by = {a.field: a for a in answers}
    assert by["_systemfield_name"].value == "Alex Rivera"
    assert by["q_sponsor"].value == "No"
    assert by["q_level"].value is None  # optional, non-sensitive, unknown -> left blank


def test_short_patterns_need_whole_words():
    f = FormField(name="q", label="Describe a stack trace you debugged", type=FieldType.TEXT, required=False)
    entry, _ = BANK.match(f)
    assert entry is None  # "race" must not match "trace"


def test_pick_option():
    opts = [Option(label="Yes, I am authorized", value="1"), Option(label="No", value="0")]
    assert pick_option("yes", opts).value == "1"
    assert pick_option("No", opts).value == "0"
    assert pick_option("Maybe", opts) is None


def test_required_cover_letter_requested():
    f = FormField(name="cover_letter", label="Cover Letter", type=FieldType.FILE, required=True)
    answers, needs_cover = run([f])
    assert needs_cover and answers[0].value == "cover_letter"


def test_cover_letter_policy_when_required_skips_optional():
    fields = greenhouse.parse_form(fixture("greenhouse_questions.json"))
    answers, needs_cover = answer_form(fields, BANK, M, FakeLLM(), "SWE", "Acme", "jd", cover_policy="when_required")
    assert not needs_cover
    assert next(a for a in answers if a.field == "cover_letter").value is None


def test_salary_uses_posted_range_else_default():
    from jobagent.answers import posted_salary

    assert posted_salary("Base pay $140,000 - $180,000") == "$140,000 – $180,000"
    assert posted_salary("Compensation: $150K – $200K • Offers Equity") == "$150,000 – $200,000"
    assert posted_salary("$55/hr, $1,000 - $2,000 signing bonus") is None
    bank = Bank.from_yaml({"questions": [{"match": ["salary expectations"], "answer": "$120,000", "use_posted_salary": True}]})
    f = FormField(name="sal", label="What are your salary expectations?", type=FieldType.TEXT, required=True)
    with_range, _ = answer_form([f], bank, M, FakeLLM(), "SWE", "Acme", "Pay range: $130,000 - $170,000")
    without, _ = answer_form([f], bank, M, FakeLLM(), "SWE", "Acme", "No pay info.")
    assert with_range[0].value == "$130,000 – $170,000"
    assert without[0].value == "$120,000"


def test_open_questions_are_written_once_then_remembered(tmp_path):
    from jobagent.answers import AnswerCache

    cache = AnswerCache(tmp_path / "learned.yaml")
    q = FormField(name="q1", label="Tell us about a project you are proud of.",
                  type=FieldType.TEXTAREA, required=False)  # optional, still worth answering
    llm = FakeLLM()
    first, _ = answer_form([q], BANK, M, llm, "AI Engineer", "Acme", "jd", cache=cache)
    assert first[0].value and first[0].source == "llm"
    assert "WrittenOutput" in llm.calls

    reused = AnswerCache(tmp_path / "learned.yaml")      # a later run, fresh process
    llm2 = FakeLLM()
    again, _ = answer_form([q], BANK, M, llm2, "AI Engineer", "Other Co", "jd", cache=reused)
    assert again[0].value == first[0].value
    assert again[0].source == "learned"
    assert "WrittenOutput" not in llm2.calls          # no second call to the model


def test_cover_letters_are_not_cached_across_jobs(tmp_path):
    from jobagent.answers import AnswerCache

    cache = AnswerCache(tmp_path / "learned.yaml")
    f = FormField(name="cover_letter", label="Cover Letter", type=FieldType.TEXTAREA, required=True)
    answer_form([f], BANK, M, FakeLLM(), "AI Engineer", "Acme", "jd", cache=cache)
    assert cache.get("Cover Letter") is None  # must stay per-company
