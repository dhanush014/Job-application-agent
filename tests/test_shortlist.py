"""The hand-apply shortlist page."""

from jobagent.models import Answer, Application, FieldType, Status
from jobagent.store.shortlist import export_shortlist


def app(**kw):
    base = dict(
        job_key="greenhouse:acme:1", ats="greenhouse", company="acme", company_name="Acme <Inc>",
        job_id="1", title="AI Engineer & Co", location="Remote - US",
        url="https://x", apply_url="https://job-boards.greenhouse.io/acme/jobs/1",
        score=91, fit_summary="Strong match.", status=Status.READY,
    )
    return Application(**{**base, **kw})


def test_page_escapes_html_and_links_each_job(tmp_path):
    index = export_shortlist([app()], tmp_path)
    page = index.read_text()
    assert "Acme &lt;Inc&gt;" in page and "<Inc>" not in page  # no raw HTML from job data
    assert "https://job-boards.greenhouse.io/acme/jobs/1" in page
    assert "91" in page


def test_answers_table_flags_what_you_must_fill_in(tmp_path):
    a = app(answers=[
        Answer(field="e", label="Email", type=FieldType.TEXT, value="me@example.com", source="profile"),
        Answer(field="s", label="Salary?", type=FieldType.TEXT, required=True, value=None),
        Answer(field="r", label="Resume", type=FieldType.FILE, value="resume"),
    ])
    page = export_shortlist([a], tmp_path).read_text()
    assert "me@example.com" in page
    assert "needs your answer" in page
    assert "Tailored resume PDF" in page


def test_jobs_are_ranked_and_both_formats_copied(tmp_path):
    pdf = tmp_path / "dhanush_sathyan_resume_acme.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    docx = tmp_path / "dhanush_sathyan_resume_acme.docx"
    docx.write_bytes(b"PK fake docx")
    low = app(job_key="k2", job_id="2", title="Backend Engineer", score=70,
              resume_pdf=str(pdf), resume_docx=str(docx))
    high = app(score=95, resume_pdf=str(pdf), resume_docx=str(docx))
    out = tmp_path / "out"
    page = export_shortlist([low, high], out).read_text()
    assert page.index("AI Engineer") < page.index("Backend Engineer")  # best first
    names = sorted(q.name for q in out.iterdir())
    assert "dhanush_sathyan_resume_acme.pdf" in names
    assert "dhanush_sathyan_resume_acme.docx" in names
    # a second role at the same company must not overwrite the first
    assert "dhanush_sathyan_resume_acme_2.pdf" in names
    assert (out / "dhanush_sathyan_resume_acme.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_applied_tracking_is_wired_to_each_job(tmp_path):
    page = export_shortlist([app(), app(job_key="k2", job_id="2", title="Backend Engineer")], tmp_path).read_text()
    assert page.count("data-mark type") == 2     # a Yes button per job
    assert "data-key='greenhouse:acme:1'" in page  # keyed so the state survives a reload
    assert "localStorage" in page and "0 of 2 applied" in page


def test_empty_shortlist_is_still_a_readable_page(tmp_path):
    page = export_shortlist([], tmp_path).read_text()
    assert "No jobs matched" in page and "<html" in page
