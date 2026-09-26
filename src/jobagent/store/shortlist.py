"""Write a shortlist folder you can work through by hand.

One self-contained folder per run: the tailored resume as both PDF and an
editable .docx (plus a cover letter when the form wants one), and an index.html
listing every job with its link, fit score and the answers to copy in. Marking a
job applied is remembered in the browser, so the page doubles as a tracker.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from ..models import Application, FieldType

CSS = """
:root { --bg:#f7f7f5; --panel:#fff; --text:#1c1c1a; --muted:#6b6b66; --line:#e3e2dd;
  --accent:#2f6fde; --accent-text:#fff; --ok:#1f8a4c; --ok-bg:#e8f5ec; --warn:#b26a00; --chip:#efeeea; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --panel:#1f1f1d; --text:#ecebe6;
  --muted:#9c9b95; --line:#33322f; --accent:#5b8ff0; --ok:#46b779; --ok-bg:#1d2f24; --warn:#e0a040; --chip:#2a2a27; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--text);
  font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
a { color:var(--accent) }
header { padding:20px 16px; border-bottom:1px solid var(--line); background:var(--panel);
  position:sticky; top:0; z-index:5 }
.wrap { max-width:900px; margin:0 auto }
header h1 { margin:0 0 3px; font-size:19px }
header p { margin:0; color:var(--muted); font-size:14px }
.bar { height:6px; border-radius:99px; background:var(--chip); margin-top:11px; overflow:hidden }
.bar i { display:block; height:100%; background:var(--ok); width:0; transition:width .25s }
main { max-width:900px; margin:0 auto; padding:18px 16px 60px }
.job { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:16px 18px; margin-bottom:14px }
.job.done { border-color:var(--ok); background:var(--ok-bg) }
.job.done .fade { opacity:.55 }
.top { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap }
.top h2 { margin:0; font-size:17px }
.score { font-variant-numeric:tabular-nums; font-weight:700; padding:2px 9px;
  border-radius:999px; background:var(--chip) }
.score.high { background:var(--ok); color:#fff }
.meta { color:var(--muted); font-size:14px }
.summary { margin:10px 0 0 }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:13px; align-items:center }
.btn { display:inline-block; padding:8px 15px; border-radius:7px; border:1px solid var(--line);
  background:var(--bg); color:var(--text); text-decoration:none; font:inherit; font-size:14px; cursor:pointer }
.btn.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-text); font-weight:600 }
.btn.mark { border-color:var(--ok); color:var(--ok); font-weight:600 }
.stamp { color:var(--ok); font-weight:600; font-size:14px }
.warn { margin:11px 0 0; padding-left:19px; color:var(--warn); font-size:14px }
details { margin-top:12px } summary { cursor:pointer; color:var(--muted); font-size:14px }
table { width:100%; border-collapse:collapse; margin-top:10px; font-size:14px }
th, td { text-align:left; padding:7px 9px; border-bottom:1px solid var(--line); vertical-align:top }
th { width:44%; font-weight:600; color:var(--muted) }
.none { color:var(--warn) }
.empty { color:var(--muted); background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:20px }
"""

# Tracking lives in this browser only: the page is a file, with nothing to POST to.
JS = """
const KEY = 'jobagent.applied';
const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
const save = (s) => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch {} };

function paint() {
  const state = load();
  let done = 0;
  document.querySelectorAll('.job').forEach(card => {
    const when = state[card.dataset.key];
    const btn = card.querySelector('.mark');
    const stamp = card.querySelector('.stamp');
    card.classList.toggle('done', !!when);
    if (when) {
      done++;
      btn.textContent = 'Undo';
      btn.classList.remove('mark');
      stamp.textContent = 'Applied ' + when;
    } else {
      btn.textContent = 'Yes, I applied';
      btn.classList.add('mark');
      stamp.textContent = '';
    }
  });
  const total = document.querySelectorAll('.job').length;
  const count = document.getElementById('count');
  if (count) count.textContent = done + ' of ' + total + ' applied';
  const bar = document.getElementById('bar');
  if (bar) bar.style.width = total ? (100 * done / total) + '%' : '0';
}

document.addEventListener('click', e => {
  const btn = e.target.closest('button[data-mark]');
  if (!btn) return;
  const key = btn.closest('.job').dataset.key;
  const state = load();
  if (state[key]) delete state[key];
  else state[key] = new Date().toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'});
  save(state);
  paint();
});

paint();
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


def _job_html(app: Application, files: dict[str, str]) -> str:
    score = app.score if app.score is not None else "–"
    high = " high" if isinstance(app.score, int) and app.score >= 85 else ""
    parts = [
        f"<article class='job' data-key='{html.escape(app.job_key)}'>",
        "<div class='fade'>",
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
    parts.append("</div>")

    buttons = [
        f"<a class='btn primary' href='{html.escape(app.apply_url)}' target='_blank' rel='noopener'>Open posting ↗</a>"
    ]
    for label, key in (("Resume PDF", "pdf"), ("Resume (Word)", "docx"), ("Cover letter", "cover")):
        if files.get(key):
            buttons.append(f"<a class='btn' href='{html.escape(files[key])}' target='_blank'>{label}</a>")
    buttons.append("<button class='btn mark' data-mark type='button'>Yes, I applied</button>")
    buttons.append("<span class='stamp'></span>")
    parts.append("<div class='actions'>" + "".join(buttons) + "</div>")

    if app.review_reasons:
        items = "".join(f"<li>{html.escape(r)}</li>" for r in app.review_reasons)
        parts.append(f"<ul class='warn fade'>{items}</ul>")
    parts.append("<details class='fade'><summary>Answers for the form</summary>" + _answer_rows(app) + "</details>")
    if app.cover_letter:
        parts.append(
            "<details class='fade'><summary>Cover letter text</summary>"
            f"<pre style='white-space:pre-wrap;font:inherit'>{html.escape(app.cover_letter)}</pre></details>"
        )
    parts.append("</article>")
    return "".join(parts)


def export_shortlist(apps: list[Application], out_dir: Path) -> Path:
    """Write out_dir with the resumes and an index.html. Returns the index path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(apps, key=lambda a: -(a.score or 0))

    body = []
    taken: set[str] = set()
    for app in ranked:
        files: dict[str, str] = {}
        # Two roles at the same company would otherwise share a filename and
        # overwrite each other, so the second one gets a suffix.
        stem = Path(app.resume_pdf).stem if app.resume_pdf else _slug(f"{app.company_name}-{app.title}")
        unique, n = stem, 2
        while unique in taken:
            unique, n = f"{stem}_{n}", n + 1
        taken.add(unique)
        for key, source, suffix in (
            ("pdf", app.resume_pdf, ".pdf"),
            ("docx", app.resume_docx, ".docx"),
            ("cover", app.cover_letter_pdf, "_cover_letter.pdf"),
        ):
            if not source or not Path(source).exists():
                continue
            name = f"{unique}{suffix}"
            shutil.copyfile(source, out_dir / name)
            files[key] = name
        body.append(_job_html(app, files))

    when = datetime.now().astimezone().strftime("%B %-d, %Y at %-I:%M %p")
    jobs = "".join(body) or "<p class='empty'>No jobs matched this run.</p>"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job shortlist — {html.escape(when)}</title>
<style>{CSS}</style></head>
<body>
<header><div class="wrap">
  <h1>{len(ranked)} job{"" if len(ranked) == 1 else "s"} to apply to</h1>
  <p><span id="count">0 of {len(ranked)} applied</span> · prepared {html.escape(when)}</p>
  <div class="bar"><i id="bar"></i></div>
</div></header>
<main>{jobs}</main>
<script>{JS}</script>
</body></html>"""
    index = out_dir / "index.html"
    index.write_text(page)
    (out_dir / "jobs.json").write_text(
        json.dumps(
            [
                {
                    "key": a.job_key, "title": a.title, "company": a.company_name,
                    "url": a.apply_url, "score": a.score,
                }
                for a in ranked
            ],
            indent=2,
        )
    )
    return index
