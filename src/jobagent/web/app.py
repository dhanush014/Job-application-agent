"""Local review dashboard: browse everything, edit answers, one-click apply."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..answers import is_cover_answer, review_reasons
from ..models import Application, FieldType, Status
from ..pipeline import Pipeline

log = logging.getLogger(__name__)

TABS = [
    (Status.NEEDS_REVIEW, "Needs review"),
    (Status.READY, "Ready"),
    (Status.DRY_RUN, "Dry run"),
    (Status.APPLYING, "Applying"),
    (Status.APPLIED, "Applied"),
    (Status.LOW_FIT, "Low fit"),
    (Status.FILTERED, "Filtered"),
    (Status.FAILED, "Failed"),
    (Status.DISMISSED, "Dismissed"),
]
ANSWER_REASON_PREFIXES = ("Needs your answer", "Low-confidence answer", "browser:")


def create_app(pipeline: Pipeline) -> FastAPI:
    app = FastAPI(title="jobagent")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    worker = ThreadPoolExecutor(max_workers=1)  # one browser at a time
    store = pipeline.store
    cfg = pipeline.cfg

    def get_or_404(app_id: str) -> Application:
        a = store.get(app_id)
        if a is None:
            raise HTTPException(404)
        return a

    def run_bg(fn, *args):
        def wrapped():
            try:
                fn(*args)
            except Exception:
                log.exception("background task failed")
        worker.submit(wrapped)

    def apply_now(app_id: str):
        a = store.get(app_id)
        if a and a.status != Status.APPLIED:
            pipeline.submit(a, headless=cfg.apply.review_headless, dry_run=False)
            pipeline.export()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, status: str = Status.NEEDS_REVIEW.value):
        apps = store.list()
        counts = {s: 0 for s, _ in TABS}
        for a in apps:
            counts[a.status] = counts.get(a.status, 0) + 1
        current = Status(status)
        rows = sorted((a for a in apps if a.status == current), key=lambda a: -(a.score or 0))
        return templates.TemplateResponse(
            request, "index.html",
            {"tabs": TABS, "counts": counts, "current": current, "rows": rows,
             "applied_today": pipeline.applied_today(), "cfg": cfg,
             "applying": counts.get(Status.APPLYING, 0)},
        )

    @app.get("/a/{app_id}", response_class=HTMLResponse)
    def detail(request: Request, app_id: str):
        a = get_or_404(app_id)
        has_cover = any(is_cover_answer(x) for x in a.answers)
        return templates.TemplateResponse(
            request, "detail.html", {"a": a, "FieldType": FieldType, "Status": Status, "has_cover": has_cover}
        )

    async def _save_answers(request: Request, a: Application) -> None:
        form = await request.form()
        for i, ans in enumerate(a.answers):
            key = f"ans__{i}"
            if key not in form or ans.type == FieldType.FILE:
                continue
            if ans.type == FieldType.MULTISELECT:
                new = [v for v in form.getlist(key) if v] or None
            else:
                new = (form.get(key) or "").strip() or None
            if new != ans.value:
                ans.value, ans.source, ans.confidence, ans.note = new, "user", 100, ""
        if "cover_letter" in form:
            new_cl = (form.get("cover_letter") or "").strip() or None
            if new_cl != a.cover_letter:
                a.cover_letter = new_cl
                for ans in a.answers:  # a letter you wrote fills fields that were left empty
                    if new_cl and is_cover_answer(ans) and ans.value is None:
                        if ans.type == FieldType.FILE:
                            ans.value, ans.source, ans.confidence = "cover_letter", "file", 100
                        elif ans.type == FieldType.TEXTAREA:
                            ans.note = "cover letter"
                a.review_reasons = [r for r in a.review_reasons if not r.startswith("cover letter PDF")]
                a.review_reasons += pipeline.attach_cover_letter(a)
        other = [r for r in a.review_reasons if not r.startswith(ANSWER_REASON_PREFIXES)]
        a.review_reasons = other + review_reasons(a.answers, cfg.apply.min_answer_confidence)
        store.save(a)

    @app.post("/a/{app_id}/save")
    async def save(request: Request, app_id: str):
        await _save_answers(request, get_or_404(app_id))
        return RedirectResponse(f"/a/{app_id}", status_code=303)

    @app.post("/a/{app_id}/apply")
    async def apply(request: Request, app_id: str):
        a = get_or_404(app_id)
        await _save_answers(request, a)
        a.status = Status.APPLYING
        store.save(a)
        run_bg(apply_now, app_id)
        return RedirectResponse(f"/a/{app_id}", status_code=303)

    @app.post("/bulk-apply")
    async def bulk_apply(request: Request):
        form = await request.form()
        status = Status(form.get("status"))
        for a in store.list([status]):
            a.status = Status.APPLYING
            store.save(a)
            run_bg(apply_now, a.id)
        return RedirectResponse(f"/?status={Status.APPLYING.value}", status_code=303)

    @app.post("/a/{app_id}/dismiss")
    def dismiss(app_id: str):
        a = get_or_404(app_id)
        a.status = Status.DISMISSED
        store.save(a)
        return RedirectResponse("/", status_code=303)

    @app.post("/a/{app_id}/prepare")
    def prepare(app_id: str):
        get_or_404(app_id)
        run_bg(pipeline.prepare_anyway, app_id)
        return RedirectResponse(f"/a/{app_id}", status_code=303)

    @app.post("/a/{app_id}/reapply")
    def reapply(app_id: str):
        get_or_404(app_id)
        run_bg(pipeline.reapply, app_id)
        return RedirectResponse(f"/?status={Status.NEEDS_REVIEW.value}", status_code=303)

    @app.post("/run")
    def run():
        run_bg(pipeline.run)
        return RedirectResponse("/", status_code=303)

    def _file(path: str | None, media: str):
        if not path or not Path(path).exists():
            raise HTTPException(404)
        return FileResponse(path, media_type=media)

    @app.get("/a/{app_id}/resume.pdf")
    def resume_pdf(app_id: str):
        return _file(get_or_404(app_id).resume_pdf, "application/pdf")

    @app.get("/a/{app_id}/cover.pdf")
    def cover_pdf(app_id: str):
        return _file(get_or_404(app_id).cover_letter_pdf, "application/pdf")

    @app.get("/a/{app_id}/screenshot.png")
    def screenshot(app_id: str):
        return _file(get_or_404(app_id).screenshot, "image/png")

    @app.get("/export.xlsx")
    def export():
        path = pipeline.export() or pipeline.cfg.data_path / "applications.xlsx"
        if not Path(path).exists():
            from ..store.export import export_xlsx

            export_xlsx(store.list(), Path(path))
        return FileResponse(path, filename="applications.xlsx")

    return app
