from conftest import FakeLLM, mock_http
from fastapi.testclient import TestClient

from jobagent.models import Status
from jobagent.pipeline import Pipeline
from jobagent.web.app import create_app


def test_dashboard_review_edit_and_one_click_apply(cfg, store):
    from test_pipeline import FakeSubmitter

    sub = FakeSubmitter()
    p = Pipeline(cfg, store, FakeLLM(), http=mock_http(), submitter=sub)
    p.run(submit=False)
    a = next(x for x in store.list([Status.READY]) if x.ats == "greenhouse")
    a.status = Status.NEEDS_REVIEW
    a.review_reasons = ["Needs your answer: “Why do you want to work at Acme?”"]
    store.save(a)
    client = TestClient(create_app(p))

    r = client.get("/?status=needs_review")
    assert r.status_code == 200 and a.title in r.text
    r = client.get(f"/a/{a.id}")
    assert r.status_code == 200 and "Apply" in r.text
    assert client.get(f"/a/{a.id}/resume.pdf").headers["content-type"] == "application/pdf"

    idx = next(i for i, x in enumerate(a.answers) if x.field == "question_1004")
    r = client.post(f"/a/{a.id}/apply", data={f"ans__{idx}": "Because I love payments."}, follow_redirects=False)
    assert r.status_code == 303
    import time

    for _ in range(50):
        if store.get(a.id).status == Status.APPLIED:
            break
        time.sleep(0.1)
    done = store.get(a.id)
    assert done.status == Status.APPLIED
    edited = done.answers[idx]
    assert edited.value == "Because I love payments." and edited.source == "user"
    assert sub.calls[-1][2] is False  # real submit, not dry run
    assert client.get("/export.xlsx").status_code == 200


def test_editing_cover_letter_rerenders_pdf(cfg, store):
    from pypdf import PdfReader

    p = Pipeline(cfg, store, FakeLLM(), http=mock_http())
    p.run(submit=False)
    a = next(x for x in store.list() if x.job_id == "5001")
    client = TestClient(create_app(p))
    new = "Dear Hiring Team,\n\nI would love to build payments with you at Acme.\n\nAlex Rivera"
    client.post(f"/a/{a.id}/save", data={"cover_letter": new})
    b = store.get(a.id)
    assert b.cover_letter == new
    assert "love to build payments" in PdfReader(b.cover_letter_pdf).pages[0].extract_text()
    assert "Cover letter" in client.get(f"/a/{a.id}").text
