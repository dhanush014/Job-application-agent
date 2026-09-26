"""Review dashboard: browse everything, edit answers, one-click apply.

Local mode (`jobagent serve`): Apply opens a browser right here and submits.
Hosted mode (Vercel): Apply queues the application; the worker submits it.
Set DASHBOARD_PASSWORD to require a password (mandatory when hosted).
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..answers import is_cover_answer, review_reasons
from ..models import Application, FieldType, Status
from ..pipeline import Pipeline

log = logging.getLogger(__name__)

TABS = [
    (Status.NEEDS_REVIEW, "Needs review"),
    (Status.READY, "Ready"),
    (Status.DRY_RUN, "Dry run"),
    (Status.QUEUED, "Queued"),
    (Status.APPLYING, "Applying"),
    (Status.APPLIED, "Applied"),
    (Status.LOW_FIT, "Low fit"),
    (Status.FILTERED, "Filtered"),
    (Status.FAILED, "Failed"),
    (Status.DISMISSED, "Dismissed"),
]
ANSWER_REASON_PREFIXES = ("Needs your answer", "Low-confidence answer", "browser:")


def _password_ok(header: str | None, password: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        _, _, given = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return False
    return hmac.compare_digest(given.encode(), password.encode())


def create_app(pipeline: Pipeline, hosted: bool = False) -> FastAPI:
    app = FastAPI(title="jobagent")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.globals["hosted"] = hosted
    worker = ThreadPoolExecutor(max_workers=1)  # one browser at a time
    store = pipeline.store
    cfg = pipeline.cfg

    @app.middleware("http")
    async def auth(request: Request, call_next):
        password = os.environ.get("DASHBOARD_PASSWORD")
        if not password:
            if hosted:
                return PlainTextResponse("Set DASHBOARD_PASSWORD in the Vercel project settings.", 503)
            return await call_next(request)
        if not _password_ok(request.headers.get("authorization"), password):
            return PlainTextResponse(
                "Password required", 401, headers={"WWW-Authenticate": 'Basic realm="jobagent"'}
            )
        return await call_next(request)

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

    def queue_or_apply(a: Application) -> None:
        if hosted:  # no browser on serverless: the worker picks it up
            a.status = Status.QUEUED
            store.save(a)
            return
        a.status = Status.APPLYING
        store.save(a)
        run_bg(apply_now, a.id)

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
             "applying": counts.get(Status.APPLYING, 0) + counts.get(Status.QUEUED, 0)},
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
        queue_or_apply(a)
        return RedirectResponse(f"/a/{app_id}", status_code=303)

    @app.post("/bulk-apply")
    async def bulk_apply(request: Request):
        form = await request.form()
        status = Status(form.get("status"))
        for a in store.list([status]):
            queue_or_apply(a)
        after = Status.QUEUED if hosted else Status.APPLYING
        return RedirectResponse(f"/?status={after.value}", status_code=303)

    @app.post("/a/{app_id}/mark-applied")
    def mark_applied(app_id: str):
        pipeline.mark_applied(get_or_404(app_id))
        return RedirectResponse(f"/a/{app_id}", status_code=303)

    @app.post("/a/{app_id}/dismiss")
    def dismiss(app_id: str):
        a = get_or_404(app_id)
        a.status = Status.DISMISSED
        store.save(a)
        return RedirectResponse("/", status_code=303)

    def _request(app_id: str, req: str, fn):
        a = get_or_404(app_id)
        if hosted:  # needs the LLM + minutes of work: leave it for the worker
            a.request = req
            store.save(a)
        else:
            run_bg(fn, app_id)

    @app.post("/a/{app_id}/prepare")
    def prepare(app_id: str):
        _request(app_id, "prepare", pipeline.prepare_anyway)
        return RedirectResponse(f"/a/{app_id}", status_code=303)

    @app.post("/a/{app_id}/reapply")
    def reapply(app_id: str):
        _request(app_id, "reapply", pipeline.reapply)
        return RedirectResponse(f"/a/{app_id}" if hosted else f"/?status={Status.NEEDS_REVIEW.value}", status_code=303)

    @app.post("/run")
    def run():
        if hosted:
            raise HTTPException(400, "runs happen on the worker")
        run_bg(pipeline.run)
        return RedirectResponse("/", status_code=303)

    def _file(app_id: str, path: str | None, name: str, media: str):
        if path and Path(path).exists():
            return FileResponse(path, media_type=media)
        data = store.download(f"{app_id}/{name}") if path else None
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type=media)

    @app.get("/a/{app_id}/resume.pdf")
    def resume_pdf(app_id: str):
        return _file(app_id, get_or_404(app_id).resume_pdf, "resume.pdf", "application/pdf")

    @app.get("/a/{app_id}/cover.pdf")
    def cover_pdf(app_id: str):
        return _file(app_id, get_or_404(app_id).cover_letter_pdf, "cover_letter.pdf", "application/pdf")

    @app.get("/a/{app_id}/screenshot.png")
    def screenshot(app_id: str):
        shot = get_or_404(app_id).screenshot
        return _file(app_id, shot, Path(shot).name if shot else "", "image/png")

    @app.get("/export.xlsx")
    def export():
        from ..store.export import export_xlsx

        path = export_xlsx(store.list(), cfg.data_path / "applications.xlsx")
        return FileResponse(path, filename="applications.xlsx")

    return app
