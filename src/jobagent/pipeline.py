"""discover → filter → score → tailor → fit resume → answer form → gates → submit."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import policy
from .answers import Bank, answer_form, is_cover_answer, review_reasons, write_cover_letter
from .apply.browser import Files, SubmitResult, Submitter
from .config import Config
from .discovery import fetch_all, fetch_form, make_client
from .llm import LLM, LLMRateLimited
from .models import Application, FieldType, Status
from .resume.render import ResumeDoesNotFit, doc_bullets, fit, render_cover_letter, save_pdf
from .scoring import rule_filter, score_job
from .store import Store
from .store.export import export_xlsx
from .tailor import tailor

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    discovered_new: int = 0
    filtered: int = 0
    low_fit: int = 0
    ready: int = 0
    needs_review: int = 0
    failed: int = 0
    submitted: int = 0
    dry_run: int = 0
    notes: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, cfg: Config, store: Store, llm: LLM | None, http=None, submitter: Submitter | None = None):
        """`llm` may be None for the hosted dashboard, which never calls it."""
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.http = http or make_client()
        self.master = cfg.master_resume()
        self._bank: Bank | None = None
        self._submitter = submitter
        self._listed: set[str] | None = None  # job keys seen in the latest discovery

    @property
    def bank(self) -> Bank:
        if self._bank is None:
            self._bank = Bank.from_yaml(self.cfg.answer_bank())
        return self._bank

    @property
    def submitter(self) -> Submitter:
        if self._submitter is None:
            from .apply.email_code import make_fetcher

            self._submitter = Submitter(
                self.cfg.data_path / "applications",
                browser_executable=self.cfg.apply.browser_executable,
                security_code_fetcher=make_fetcher(self.cfg.email),
                manual_finish_seconds=self.cfg.apply.manual_finish_seconds,
            )
        return self._submitter

    # ---------------------------------------------------------------- stages

    def discover(self) -> int:
        jobs = fetch_all(self.http, self.cfg.companies())
        self._listed = {j.key for j in jobs}
        return sum(self.store.upsert_job(j) for j in jobs)

    def recover_stuck(self, older_than_minutes: int = 30) -> int:
        """Applications left in APPLYING by a crash go back to review."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        n = 0
        for a in self.store.list([Status.APPLYING]):
            if a.updated_at < cutoff:
                a.status = Status.NEEDS_REVIEW
                a.review_reasons = ["submission was interrupted; check the company's portal before re-applying"]
                self.store.save(a)
                n += 1
        return n

    def process(self, stats: RunStats | None = None) -> RunStats:
        stats = stats or RunStats()
        prepared = 0
        todo = self.store.jobs_to_process(self.cfg.apply.allow_reapply, self.cfg.apply.reapply_after_days)
        if self._listed is not None:  # skip postings that have since been taken down
            todo = [(j, n) for j, n in todo if j.key in self._listed]
        for job, attempt in todo:
            app = Application.from_job(job, attempt)
            if reason := rule_filter(job, self.cfg.preferences):
                app.status, app.review_reasons = Status.FILTERED, [reason]
                self.store.save(app)
                stats.filtered += 1
                continue
            if prepared >= self.cfg.apply.max_prepare_per_run:
                stats.notes.append("hit max_prepare_per_run; the rest will be handled next run")
                break
            try:
                if not self.score(app):
                    stats.low_fit += 1
                    continue
                self.prepare(app)
                prepared += 1
            except LLMRateLimited:
                stats.notes.append("Groq rate limit reached; stopping preparation for this run")
                break
            except Exception as e:
                log.exception("preparing %s failed", job.key)
                app.status, app.error = Status.FAILED, f"{type(e).__name__}: {e}"
                self.store.save(app)
                stats.failed += 1
                continue
            if app.status == Status.READY:
                stats.ready += 1
            else:
                stats.needs_review += 1
        return stats

    def score(self, app: Application) -> bool:
        """Score and save. Returns True when the job is worth applying to."""
        s = score_job(
            self.llm, app.to_job(), self.master, self.cfg.llm.max_jd_chars,
            notes=self.cfg.preferences.candidate_notes,
        )
        app.score, app.fit_summary = s.score, s.summary
        app.matched, app.gaps, app.jd_keywords = s.matched, s.dealbreakers + s.gaps, s.jd_keywords
        if s.score < self.cfg.preferences.min_score or s.dealbreakers:
            app.status = Status.LOW_FIT
            app.review_reasons = [f"fit score {s.score}"] + [f"dealbreaker: {d}" for d in s.dealbreakers]
            self.store.save(app)
            return False
        return True

    def prepare(self, app: Application) -> Application:
        """Tailor resume, answer the form, run gates. Ends READY or NEEDS_REVIEW."""
        job = app.to_job()
        reasons = policy.check_job(job)
        out_dir = self.cfg.data_path / "applications" / app.id

        # 1. resume
        t = tailor(self.llm, self.master, job.description, app.jd_keywords, self.cfg.llm.max_jd_chars)
        try:
            fr = fit(
                self.master, t.ranking, t.texts, t.skills,
                bold_priority=app.jd_keywords + t.skills,  # the job's own tool names first
                relevance=t.relevance, min_relevance=self.cfg.preferences.min_bullet_relevance,
            )
        except ResumeDoesNotFit as e:
            reasons.append(f"tailored resume did not fit ({e}); used master ordering")
            fr = fit(self.master, [b.id for b in self.master.iter_bullets()])
        hard = [w for w in fr.warnings if "pages" in w or "extractable" in w]
        reasons += [f"resume check failed: {w}" for w in hard]
        pdf = save_pdf(fr.render, out_dir / "resume.pdf")
        self.store.upload(pdf, f"{app.id}/resume.pdf")
        app.resume_json = fr.doc.model_dump(mode="json")
        app.resume_pdf = str(pdf)

        # 2. form
        try:
            fields = fetch_form(self.http, job)
        except Exception as e:
            fields = []
            reasons.append(f"could not load the application form schema: {e}")
        reasons += policy.check_fields(fields)
        answers, needs_cover = answer_form(
            fields, self.bank, self.master, self.llm, job.title, job.company_name, job.description,
            cover_policy=self.cfg.apply.cover_letter,
        )
        app.answers = answers
        if needs_cover:
            app.cover_letter = write_cover_letter(
                self.llm, self.master, doc_bullets(fr.doc), job.title, job.company_name, job.description
            )
            reasons += self.attach_cover_letter(app)
        reasons += review_reasons(answers, self.cfg.apply.min_answer_confidence)

        app.review_reasons = reasons
        app.status = Status.NEEDS_REVIEW if reasons else Status.READY
        app.error = None
        self.store.save(app)
        return app

    def attach_cover_letter(self, app: Application) -> list[str]:
        """Put app.cover_letter into every cover letter field: pasted into text
        boxes, rendered to a PDF for uploads. Called again after dashboard edits."""
        text = app.cover_letter
        reasons = []
        for a in app.answers:
            if not is_cover_answer(a):
                continue
            if not text:
                if a.value == "cover_letter" or a.note == "cover letter":
                    a.value, a.note = None, "cover letter could not be generated"
                continue
            if a.type == FieldType.TEXTAREA and (a.note == "cover letter" or a.source == "llm"):
                a.value, a.source, a.confidence, a.note = text, "llm", 90, ""
            elif a.type == FieldType.FILE and a.value == "cover_letter":
                try:
                    pdf = render_cover_letter(
                        self.master.contact, app.title, app.company_name, text,
                        self.cfg.data_path / "applications" / app.id / "cover_letter.pdf",
                    )
                except ValueError as e:
                    reasons.append(f"cover letter PDF: {e}")
                    continue
                app.cover_letter_pdf = str(pdf)
                self.store.upload(pdf, f"{app.id}/cover_letter.pdf")
        return reasons

    # ------------------------------------------------------------- submitting

    def applied_today(self) -> int:
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.store.count_applied_since(midnight.astimezone(timezone.utc))

    def submit_ready(self, stats: RunStats | None = None) -> RunStats:
        stats = stats or RunStats()
        if not self.cfg.apply.auto_submit:
            return stats
        statuses = [Status.READY] if self.cfg.apply.dry_run else [Status.READY, Status.DRY_RUN]
        queue = sorted(self.store.list(statuses), key=lambda a: -(a.score or 0))
        if self.cfg.apply.dry_run:
            queue = [a for a in queue if a.status == Status.READY]
        remaining = self.cfg.apply.daily_limit - self.applied_today()
        if remaining <= 0:
            stats.notes.append("daily limit reached")
            return stats
        for i, app in enumerate(queue[:remaining]):
            if i:
                time.sleep(random.uniform(*self.cfg.apply.delay_seconds))
            res = self.submit(app, headless=self.cfg.apply.headless, dry_run=self.cfg.apply.dry_run)
            if res.outcome == "submitted":
                stats.submitted += 1
            elif res.outcome == "dry_run" and app.status == Status.DRY_RUN:
                stats.dry_run += 1
            else:
                stats.needs_review += 1
        return stats

    def _local_file(self, app: Application, recorded: str | None, name: str) -> Path | None:
        """The PDF on this machine, downloading it from remote storage if the
        application was prepared elsewhere (e.g. another worker run)."""
        if recorded and Path(recorded).exists():
            return Path(recorded)
        if not recorded:
            return None
        data = self.store.download(f"{app.id}/{name}")
        if data is None:
            return None
        path = self.cfg.data_path / "applications" / app.id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def submit(self, app: Application, *, headless: bool, dry_run: bool) -> SubmitResult:
        app.status = Status.APPLYING
        self.store.save(app)
        resume = self._local_file(app, app.resume_pdf, "resume.pdf")
        if resume is None:
            app.status, app.error = Status.NEEDS_REVIEW, "resume PDF not found"
            app.review_reasons = ["resume PDF not found; use Prepare anyway to rebuild it"]
            self.store.save(app)
            return SubmitResult("error", "resume PDF not found")
        files = Files(
            resume=resume,
            cover_letter=self._local_file(app, app.cover_letter_pdf, "cover_letter.pdf"),
        )
        res = self.submitter.submit(app, files, headless=headless, dry_run=dry_run)
        app.screenshot = res.screenshot
        if res.outcome == "submitted":
            app.status, app.applied_at, app.error, app.review_reasons = Status.APPLIED, datetime.now(timezone.utc), None, []
        elif res.outcome == "dry_run" and not res.problems:
            app.status, app.error = Status.DRY_RUN, None
        else:
            app.status, app.error = Status.NEEDS_REVIEW, res.message
            app.review_reasons = [f"browser: {p}" for p in (res.problems or [res.message])]
        if res.screenshot:
            try:
                self.store.upload(Path(res.screenshot), f"{app.id}/{Path(res.screenshot).name}")
            except Exception:
                log.warning("screenshot upload failed", exc_info=True)
        self.store.save(app)
        return res

    def mark_applied(self, app: Application, note: str = "applied by hand") -> None:
        app.status, app.applied_at, app.error = Status.APPLIED, datetime.now(timezone.utc), None
        app.review_reasons = [note]
        self.store.save(app)

    def process_queue(self, stats: RunStats | None = None) -> RunStats:
        """Work the hosted dashboard asked for: queued Apply clicks (these skip
        the daily limit, you asked for them) and prepare / re-apply requests."""
        stats = stats or RunStats()
        for app in self.store.list():
            if not app.request:
                continue
            req, app.request = app.request, None
            self.store.save(app)
            try:
                if req == "prepare":
                    self.prepare_anyway(app.id)
                else:
                    self.reapply(app.id)
            except LLMRateLimited:
                app.request = req  # try again next time
                self.store.save(app)
                stats.notes.append("Groq rate limit reached; queued requests will retry")
                break
            except Exception as e:
                log.exception("request %s for %s failed", req, app.id)
                app.error = f"{req} failed: {e}"
                self.store.save(app)
        for app in self.store.list([Status.QUEUED]):
            res = self.submit(app, headless=self.cfg.apply.headless, dry_run=False)
            if res.outcome == "submitted":
                stats.submitted += 1
            else:
                stats.needs_review += 1
        return stats

    def reapply(self, app_id: str) -> Application:
        """Fresh attempt at a job you already applied to (re-tailored, re-answered)."""
        old = self.store.get(app_id)
        if old is None:
            raise KeyError(app_id)
        app = Application.from_job(old.to_job(), attempt=old.attempt + 1)
        self.score(app)
        return self.prepare(app)

    def prepare_anyway(self, app_id: str) -> Application:
        """Prepare a filtered / low-fit job because you want it anyway."""
        app = self.store.get(app_id)
        if app is None:
            raise KeyError(app_id)
        if app.score is None:
            self.score(app)
        return self.prepare(app)

    def export(self) -> Path | None:
        if not self.cfg.storage.excel_path:
            return None
        return export_xlsx(self.store.list(), self.cfg.path(self.cfg.storage.excel_path))

    def run(self, submit: bool = True) -> RunStats:
        stats = RunStats()
        self.recover_stuck()
        self.process_queue(stats)
        stats.discovered_new = self.discover()
        self.process(stats)
        if submit:
            self.submit_ready(stats)
        self.export()
        return stats
