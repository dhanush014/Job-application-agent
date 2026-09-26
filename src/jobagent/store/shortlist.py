"""Write a shortlist folder you can work through by hand.

One self-contained folder per run: the tailored resume (and cover letter) as
PDFs, plus an index.html listing each job with its link, fit score and the
answers to copy into the form. No browser, no submitting.
"""

from __future__ import annotations

import html
import re
import shutil
from datetime import datetime
from pathlib import Path

from ..models import Application, FieldType

CSS = """
:root { --bg:#f7f7f5; --panel:#fff; --text:#1c1c1a; --muted:#6b6b66; --line:#e3e2dd;
  --accent:#2f6fde; --accent-text:#fff; --ok:#1f8a4c; --warn:#b26a00; --chip:#efeeea; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --panel:#1f1f1d; --text:#ecebe6;
  --muted:#9c9b95; --line:#33322f; --accent:#5b8ff0; --ok:#46b779; --warn:#e0a040; --chip:#2a2a27; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--text);
  font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
a { color:var(--accent) }
header { padding:22px 16px; border-bottom:1px solid var(--line); background:var(--panel) }
header h1 { margin:0 0 4px; font-size:19px }
header p { margin:0; color:var(--muted); font-size:14px }
main { max-width:900px; margin:0 auto; padding:18px 16px 60px }
.job { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:16px 18px; margin-bottom:14px }
.top { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap }
.top h2 { margin:0; font-size:17px }
.score { font-variant-numeric:tabular-nums; font-weight:700; padding:2px 9px;
  border-radius:999px; background:var(--chip) }
.score.high { background:var(--ok); color:#fff }
.meta { color:var(--muted); font-size:14px }
.summary { margin:10px 0 0 }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:13px }
.btn { display:inline-block; padding:8px 15px; border-radius:7px; border:1px solid var(--line);
  background:var(--bg); color:var(--text); text-decoration:none; font-size:14px }
.btn.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-text); font-weight:600 }
.warn { margin:11px 0 0; padding-left:19px; color:var(--warn); font-size:14px }
details { margin-top:12px } summary { cursor:pointer; color:var(--muted); font-size:14px }
table { width:100%; border-collapse:collapse; margin-top:10px; font-size:14px }
th, td { text-align:left; padding:7px 9px; border-bottom:1px solid var(--line); vertical-align:top }
th { width:44%; font-weight:600; color:var(--muted) }
.none { color:var(--warn) }
.empty { color:var(--muted); background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:20px }
"""


def _slug(text: str, limit: int = 45) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip()
    return re.sub(r"[\s_-]+", "-", s)[:limit].strip("-") or "job"


def _answer_rows(app: Application) -> str:
    rows = []
    for a in app.answers:
        if a.type == FieldType.FILE:
            value = "Tailored resume PDF" if a.value == "resume" else "Cover letter PDF" if a.value else "—"
            rows.append((a.label, value, False))
            continue
        value = ", ".join(a.value) if isinstance(a.value, list) else a.value
        rows.append((a.label, value or "needs your answer", not value))
    if not rows:
        return "<p class='meta'>The form's questions could not be read; check the posting.</p>"
    cells = "".join(
        f"<tr><th>{html.escape(label)}</th>"
        f"<td{' class=none' if missing else ''}>{html.escape(str(value))}</td></tr>"
        for label, value, missing in rows
    )
    return f"<table>{cells}</table>"


def _job_html(app: Application, resume: str | None, cover: str | None) -> str:
    score = app.score if app.score is not None else "–"
    high = " high" if isinstance(app.score, int) and app.score >= 85 else ""
    parts = [
        "<article class='job'>",
        "<div class='top'>",
        f"<h2>{html.escape(app.title)}</h2>",
        f"<span class='meta'>{html.escape(app.company_name)}"
        + (f" · {html.escape(app.location)}" if app.location else "")
        + "</span>",
        f"<span class='score{high}'>{score}</span>",
        "</div>",
    ]
    if app.fit_summary:
        parts.append(f"<p class='summary'>{html.escape(app.fit_summary)}</p>")
    buttons = [f"<a class='btn primary' href='{html.escape(app.apply_url)}' target='_blank' rel='noopener'>Open posting ↗</a>"]
    if resume:
        buttons.append(f"<a class='btn' href='{html.escape(resume)}' target='_blank'>Resume PDF</a>")
    if cover:
        buttons.append(f"<a class='btn' href='{html.escape(cover)}' target='_blank'>Cover letter</a>")
    parts.append("<div class='actions'>" + "".join(buttons) + "</div>")
    if app.review_reasons:
        items = "".join(f"<li>{html.escape(r)}</li>" for r in app.review_reasons)
        parts.append(f"<ul class='warn'>{items}</ul>")
    parts.append(
        "<details><summary>Answers for the form</summary>" + _answer_rows(app) + "</details>"
    )
    if app.cover_letter:
        parts.append(
            "<details><summary>Cover letter text</summary>"
            f"<pre style='white-space:pre-wrap;font:inherit'>{html.escape(app.cover_letter)}</pre></details>"
        )
    parts.append("</article>")
    return "".join(parts)


def export_shortlist(apps: list[Application], out_dir: Path) -> Path:
    """Write out_dir with the PDFs and an index.html. Returns the index path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(apps, key=lambda a: -(a.score or 0))

    body = []
    for i, app in enumerate(ranked, start=1):
        stem = f"{i:02d}_{_slug(app.company_name)}_{_slug(app.title)}"
        resume = cover = None
        if app.resume_pdf and Path(app.resume_pdf).exists():
            resume = f"{stem}.pdf"
            shutil.copyfile(app.resume_pdf, out_dir / resume)
        if app.cover_letter_pdf and Path(app.cover_letter_pdf).exists():
            cover = f"{stem}_cover-letter.pdf"
            shutil.copyfile(app.cover_letter_pdf, out_dir / cover)
        body.append(_job_html(app, resume, cover))

    when = datetime.now().astimezone().strftime("%B %-d, %Y at %-I:%M %p")
    jobs = "".join(body) or "<p class='empty'>No jobs matched this run.</p>"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job shortlist — {html.escape(when)}</title>
<style>{CSS}</style></head>
<body>
<header>
  <h1>{len(ranked)} job{"" if len(ranked) == 1 else "s"} to apply to</h1>
  <p>Prepared {html.escape(when)}. Each one has a resume tailored to that posting.</p>
</header>
<main>{jobs}</main>
</body></html>"""
    index = out_dir / "index.html"
    index.write_text(page)
    return index
