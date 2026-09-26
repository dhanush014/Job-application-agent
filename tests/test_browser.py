"""Filler tests against a local page shaped like a Greenhouse form."""

import os
from pathlib import Path

import pytest

from jobagent.apply import browser
from jobagent.models import Answer, FieldType

FIX = Path(__file__).parent / "fixtures"
EXE = os.environ.get("JOBAGENT_BROWSER") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)


@pytest.fixture
def page():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=EXE) if EXE else p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        pg = b.new_page()
        pg.set_default_timeout(5000)
        pg.goto((FIX / "greenhouse_form.html").as_uri())
        yield pg
        b.close()


def test_fill_verify_and_submit(page, tmp_path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    answers = [
        Answer(field="first_name", label="First Name", type=FieldType.TEXT, required=True, value="Alex"),
        Answer(field="email", label="Email", type=FieldType.TEXT, required=True, value="a@b.co"),
        Answer(field="resume", label="Resume/CV", type=FieldType.FILE, required=True, value="resume"),
        Answer(field="question_1002", label="Are you legally authorized to work in the United States?",
               type=FieldType.SELECT, required=True, value="Yes"),
        Answer(field="question_1005", label="Which of these languages have you used professionally?",
               type=FieldType.MULTISELECT, required=True, value=["Go", "Rust"]),
        Answer(field="question_1004", label="Why do you want to work at Acme?", type=FieldType.TEXTAREA,
               required=True, value="Payments are fun."),
    ]
    problems = browser.fill_form(page, answers, browser.Files(resume=pdf))
    assert problems == []
    assert page.input_value("#first_name") == "Alex"
    assert page.eval_on_selector("#question_1002", "e => e.options[e.selectedIndex].text") == "Yes"
    assert page.locator("input[type=checkbox]:checked").count() == 2
    assert page.eval_on_selector("#resume", "e => e.files.length") == 1
    assert not browser.detect_captcha(page)
    browser.click_submit(page)
    assert browser.wait_confirmation(page, 5)


def test_missing_required_answer_reported(page, tmp_path):
    answers = [Answer(field="last_name", label="Last Name", type=FieldType.TEXT, required=True, value=None)]
    problems = browser.fill_form(page, answers, browser.Files(resume=tmp_path / "r.pdf"))
    assert problems == ["no answer for required question: Last Name"]


def test_ashby_style_yes_no_buttons_and_radios(page):
    page.goto((FIX / "ashby_form.html").as_uri())
    answers = [
        Answer(field="_systemfield_name", label="Name", type=FieldType.TEXT, required=True, value="Alex Rivera"),
        Answer(field="q_sponsor", label="Do you require visa sponsorship?", type=FieldType.BOOLEAN, required=True, value="No"),
        Answer(field="q_team", label="Preferred team", type=FieldType.SELECT, value="Growth"),
    ]
    assert browser.fill_form(page, answers, browser.Files(resume=Path("x.pdf"))) == []
    assert page.eval_on_selector(".yesno", "e => e.dataset.v") == "no"
    assert page.is_checked("input[value=growth]")
