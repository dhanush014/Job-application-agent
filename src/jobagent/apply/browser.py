"""Playwright form filling + submission for Greenhouse and Ashby hosted forms.

Everything is best-effort: when a field cannot be filled and verified, the
application is routed to the review queue (with a screenshot) rather than
submitted half-filled. In headed "review" mode the browser stays open so you
can fix the form and press submit yourself; the agent detects the
confirmation page and records the application as applied.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from ..models import Answer, Application, FieldType

log = logging.getLogger(__name__)

Outcome = Literal["submitted", "dry_run", "needs_human", "error"]


@dataclass
class SubmitResult:
    outcome: Outcome
    message: str = ""
    screenshot: str | None = None
    problems: list[str] = field(default_factory=list)


@dataclass
class Files:
    resume: Path
    cover_letter: Path | None = None


CONFIRM_TEXT = re.compile(
    r"thank you for (applying|your application|your interest)|application (has been |was )?(successfully )?(submitted|received)|we('ve| have) received your application",
    re.I,
)
SUBMIT_BUTTON = re.compile(r"^\s*submit( application)?\s*$", re.I)


class Submitter:
    def __init__(
        self,
        screenshots_dir: Path,
        browser_executable: str | None = None,
        security_code_fetcher: Callable[[float], str | None] | None = None,
        manual_finish_seconds: int = 600,
    ):
        self.screens = screenshots_dir
        self.exe = browser_executable
        self.fetch_code = security_code_fetcher
        self.manual_finish_seconds = manual_finish_seconds

    def submit(self, app: Application, files: Files, *, headless: bool, dry_run: bool) -> SubmitResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            kw = {"headless": headless}
            if self.exe:
                kw["executable_path"] = self.exe
            browser = p.chromium.launch(**kw)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(15_000)
            try:
                return self._run(page, app, files, headless=headless, dry_run=dry_run)
            except Exception as e:  # never crash the pipeline on a browser problem
                log.exception("submit failed for %s", app.id)
                return SubmitResult("error", f"{type(e).__name__}: {e}", self._shot(page, app, "error"))
            finally:
                browser.close()

    # ------------------------------------------------------------------

    def _run(self, page, app: Application, files: Files, *, headless: bool, dry_run: bool) -> SubmitResult:
        page.goto(app.apply_url, wait_until="domcontentloaded")
        open_form(page, app.ats)
        problems = fill_form(page, app.answers, files)
        if detect_captcha(page):
            problems.append("CAPTCHA challenge on the page")
        if dry_run:
            shot = self._shot(page, app, "filled")
            if not headless:  # trial run: leave the filled form open for you to inspect
                log.info("dry run: review the filled form, then close the browser window (max %ss)", self.manual_finish_seconds)
                wait_closed(page, self.manual_finish_seconds)
            return SubmitResult("dry_run", "filled; submit not clicked (dry run)", shot, problems)
        if problems and headless:
            return SubmitResult("needs_human", "; ".join(problems), self._shot(page, app, "problems"), problems)
        if not problems:
            started = time.time()
            click_submit(page)
            self._maybe_security_code(page, started)
            if wait_confirmation(page, 20):
                return SubmitResult("submitted", "confirmation page detected", self._shot(page, app, "confirmation"))
            errs = visible_errors(page)
            if headless:
                return SubmitResult("needs_human", "no confirmation after submit: " + "; ".join(errs or ["unknown"]),
                                    self._shot(page, app, "after-submit"), errs)
            problems = errs or ["no confirmation after submit"]
        # headed: hand over to the human and wait for them to finish
        log.info("waiting up to %ss for you to finish the form in the browser", self.manual_finish_seconds)
        if wait_confirmation(page, self.manual_finish_seconds):
            return SubmitResult("submitted", "submitted after manual finish", self._shot(page, app, "confirmation"))
        return SubmitResult("needs_human", "; ".join(problems), self._shot(page, app, "timeout"), problems)

    def _maybe_security_code(self, page, since: float) -> None:
        box = page.locator("input[id^='security-input'], input[name*='security_code' i], input[id*='security-code' i]")
        try:
            box.first.wait_for(state="visible", timeout=6000)
        except Exception:
            return
        if not self.fetch_code:
            return
        code = self.fetch_code(since)
        if not code:
            return
        n = box.count()
        if n > 1:
            for i, ch in enumerate(code[:n]):
                box.nth(i).fill(ch)
        else:
            box.first.fill(code)
        click_submit(page)

    def _shot(self, page, app: Application, tag: str) -> str | None:
        try:
            path = self.screens / app.id / f"{tag}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return None


# ----------------------------------------------------------------------
# DOM helpers (module level so they can be tested against fixture pages)
# ----------------------------------------------------------------------


def open_form(page, ats: str) -> None:
    """Some boards show the description first and the form behind a button."""
    if page.locator("form input, form textarea").count():
        return
    btn = page.get_by_role("button", name=re.compile(r"^apply", re.I)).or_(
        page.get_by_role("link", name=re.compile(r"^apply", re.I))
    )
    if btn.count():
        btn.first.click()
        page.wait_for_timeout(1000)
    if ats == "ashby" and not page.locator("form input").count():
        tab = page.get_by_role("tab", name=re.compile("application", re.I))
        if tab.count():
            tab.first.click()
    page.locator("form").first.wait_for(timeout=20_000)


def _by_field(page, name: str):
    return page.locator(f"[id='{name}'], [name='{name}']").first


def _question_box(page, a: Answer, has):
    """Innermost element that contains both the question text and `has`."""
    return page.locator("fieldset, div").filter(has_text=a.label[:60]).filter(has=has).last


def fill_form(page, answers: list[Answer], files: Files) -> list[str]:
    problems = []
    for a in answers:
        if a.value in (None, "", []):
            if a.required:
                problems.append(f"no answer for required question: {a.label}")
            continue
        try:
            ok = _fill_one(page, a, files)
        except Exception as e:
            ok = False
            log.debug("fill failed for %s: %s", a.field, e)
        if not ok:
            problems.append(f"could not fill: {a.label}")
    return problems


def _fill_one(page, a: Answer, files: Files) -> bool:
    if a.type == FieldType.FILE:
        path = files.resume if a.value == "resume" else files.cover_letter
        if not path:
            return False
        inp = page.locator(f"input[type=file][id='{a.field}'], input[type=file][name='{a.field}']")
        if not inp.count():
            inp = _question_box(page, a, page.locator("input[type=file]")).locator("input[type=file]")
        if not inp.count() and a.value == "resume":
            inp = page.locator("input[type=file]")
        inp.first.set_input_files(str(path))
        return True

    if a.type in (FieldType.TEXT, FieldType.TEXTAREA):
        loc = _by_field(page, a.field)
        if not loc.count():
            loc = page.get_by_label(a.label, exact=False).first
        loc.fill(str(a.value))
        if loc.get_attribute("role") == "combobox":  # location autocompletes
            page.wait_for_timeout(1200)
            opt = page.get_by_role("option").first
            if opt.count():
                opt.click()
            return True
        return loc.input_value().strip() == str(a.value).strip()

    values = a.value if isinstance(a.value, list) else [a.value]
    return all(_choose(page, a, str(v)) for v in values)


def _choose(page, a: Answer, label: str) -> bool:
    loc = _by_field(page, a.field)
    tag = loc.evaluate("e => e.tagName.toLowerCase()") if loc.count() else ""
    if tag == "select":
        loc.select_option(label=label)
        return True
    # radios / checkboxes / yes-no buttons inside the question's container
    for role in ("radio", "checkbox", "button"):
        box = _question_box(page, a, page.get_by_role(role, name=label, exact=True))
        if not box.count():
            continue
        el = box.get_by_role(role, name=label, exact=True).first
        if role == "button":
            el.click()
            return True
        el.check()
        return el.is_checked()
    # react-select style combobox
    combo = loc if tag == "input" else _question_box(page, a, page.get_by_role("combobox")).get_by_role("combobox")
    if combo.count():
        combo.first.click()
        combo.first.fill(label)
        option = page.get_by_role("option", name=label, exact=True)
        if not option.count():
            option = page.get_by_role("option").filter(has_text=label)
        option.first.click(timeout=5000)  # raises if the option never appears
        return True
    return False


def click_submit(page) -> None:
    btn = page.get_by_role("button", name=SUBMIT_BUTTON)
    if not btn.count():
        btn = page.locator("button[type=submit], input[type=submit]")
    btn.first.click()


def wait_confirmation(page, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if "confirmation" in page.url or CONFIRM_TEXT.search(page.inner_text("body")):
                return True
        except Exception:
            pass  # page navigating, or closed by the user
        page.wait_for_timeout(1000)
    return False


def wait_closed(page, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and not page.is_closed():
        try:
            page.wait_for_timeout(1000)
        except Exception:
            return  # closed by the user


def visible_errors(page) -> list[str]:
    errs = page.locator("[role=alert], .error, .field-error, [class*='error' i]:visible")
    out = []
    for i in range(min(errs.count(), 8)):
        t = errs.nth(i).inner_text().strip()
        if t and t not in out:
            out.append(t[:120])
    return out


def detect_captcha(page) -> bool:
    challenge = page.locator(
        "iframe[src*='recaptcha'][src*='bframe'], iframe[src*='hcaptcha'][src*='challenge'], iframe[title*='challenge' i]"
    )
    return any(challenge.nth(i).is_visible() for i in range(challenge.count()))
