"""Render a ResumeDoc with the locked Typst template and fit it to one page.

The LLM supplies a *ranking* of bullets. This module decides what fits:
every role keeps its `min_bullets`, then the highest-ranked remaining
bullets are added until one more would spill onto page two (binary search
on the real rendered output, so the guarantee holds for any font metrics).
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import typst
from pypdf import PdfReader

from ..models import DocEntry, MasterResume, ResumeDoc, SkillLine

SKILL_LINE_CHARS = 105  # one line at 10pt on letter paper with 0.5in margins
PAGE_HEIGHT_PT = 792.0
MARGIN_PT = 0.45 * 72
MIN_FILL = 0.80
# A wrapped bullet whose last line is under this fraction of the width leaves
# an ugly one-or-two-word "dangling" line.
DANGLING_FRACTION = 0.22


class ResumeDoesNotFit(RuntimeError):
    pass


@dataclass
class RenderResult:
    pdf: bytes
    pages: int
    fill: float  # 0..1 fraction of the first page's content area used
    text: str
    line_ratios: list[float] = field(default_factory=list)  # per bullet, doc order

    def dangling(self) -> list[int]:
        """Indexes (doc order) of bullets whose last wrapped line is tiny."""
        out = []
        for i, r in enumerate(self.line_ratios):
            frac = r - int(r)
            if r > 1 and 0 < frac < DANGLING_FRACTION:
                out.append(i)
        return out


@dataclass
class FitResult:
    doc: ResumeDoc
    render: RenderResult
    included: list[str]
    dropped: list[str]
    warnings: list[str] = field(default_factory=list)


def _template() -> str:
    return resources.files("jobagent.resume").joinpath("template.typ").read_text()


def render(doc: ResumeDoc) -> RenderResult:
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        (tmpd / "data.json").write_text(doc.model_dump_json())
        tpl = tmpd / "resume.typ"
        tpl.write_text(_template())
        pdf = typst.compile(str(tpl))
        marker = json.loads(typst.query(str(tpl), "<end>"))
        widths = json.loads(typst.query(str(tpl), "<widths>"))
    reader = PdfReader(io.BytesIO(pdf))
    pages = len(reader.pages)
    fill = 1.0
    if marker:
        pos = marker[-1]["value"]
        y = float(str(pos["y"]).removesuffix("pt"))
        usable = PAGE_HEIGHT_PT - 2 * MARGIN_PT
        fill = 1.0 if pos["page"] > 1 else max(0.0, min(1.0, (y - MARGIN_PT) / usable))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    ratios = [float(x) for x in widths[0]["value"]] if widths else []
    return RenderResult(pdf=pdf, pages=pages, fill=fill, text=text, line_ratios=ratios)


def _skill_lines(master: MasterResume, order: list[str]) -> list[SkillLine]:
    pos = {s.lower(): i for i, s in enumerate(order)}
    lines = []
    for cat, items in master.skills.items():
        ranked = sorted(items, key=lambda s: (pos.get(s.lower(), 10_000), items.index(s)))
        budget = SKILL_LINE_CHARS - len(cat) - 2
        kept: list[str] = []
        for s in ranked:
            if len(", ".join(kept + [s])) > budget:
                continue
            kept.append(s)
        lines.append(SkillLine(category=cat, items=", ".join(kept)))
    return lines


def build_doc(
    master: MasterResume,
    chosen: list[str],
    texts: dict[str, str] | None = None,
    skills_order: list[str] | None = None,
) -> ResumeDoc:
    """`chosen` is in relevance order; bullets appear most-relevant-first per role."""
    texts = texts or {}
    rank = {bid: i for i, bid in enumerate(chosen)}
    index = master.bullet_index()

    def bullets_for(bs) -> list[str]:
        picked = sorted((b for b in bs if b.id in rank), key=lambda b: rank[b.id])
        return [texts.get(b.id, index[b.id].text) for b in picked]

    roles = [
        DocEntry(
            heading=r.company,
            subheading=r.title,
            location=r.location,
            dates=f"{r.start} – {r.end}",
            bullets=bullets_for(r.bullets),
        )
        for r in master.roles
    ]
    projects = [
        DocEntry(heading=p.name, subheading=p.link, bullets=bullets_for(p.bullets))
        for p in master.projects
    ]
    projects = [p for p in projects if p.bullets]
    return ResumeDoc(
        contact=master.contact,
        education=master.education,
        roles=roles,
        projects=projects,
        skills=_skill_lines(master, skills_order or []),
    )


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def verify(doc: ResumeDoc, result: RenderResult) -> list[str]:
    """Hard checks on the final PDF. Returns a list of problems (empty = ok)."""
    problems = []
    if result.pages != 1:
        problems.append(f"resume is {result.pages} pages")
    text = _norm(result.text)
    if _norm(doc.contact.name) not in text:
        problems.append("name not extractable from PDF")
    for entry in doc.roles + doc.projects:
        for b in entry.bullets:
            probe = _norm(b)[:30]
            if probe and probe not in text:
                problems.append(f"bullet not extractable from PDF: {b[:40]}…")
    return problems


def doc_bullets(doc: ResumeDoc) -> list[str]:
    return [b for e in doc.roles + doc.projects for b in e.bullets]


def fit(
    master: MasterResume,
    ranking: list[str],
    texts: dict[str, str] | None = None,
    skills_order: list[str] | None = None,
) -> FitResult:
    """Fit to one page. Rewritten bullets that leave a dangling last line are
    reverted to the master wording and the page is re-fitted once."""
    texts = dict(texts or {})
    res = _fit_once(master, ranking, texts, skills_order)
    index = master.bullet_index()
    by_text = {texts.get(b, index[b].text): b for b in res.included}
    reverted = []
    for i in res.render.dangling():
        bid = by_text.get(doc_bullets(res.doc)[i])
        if bid and bid in texts and texts[bid] != index[bid].text:
            texts.pop(bid)
            reverted.append(bid)
    if reverted:
        res = _fit_once(master, ranking, texts, skills_order)
        res.warnings.append(f"reverted {len(reverted)} rewrite(s) that left a dangling line")
    bullets = doc_bullets(res.doc)
    for i in res.render.dangling():
        res.warnings.append(f"dangling last line (shorten this master bullet): {bullets[i][:60]}…")
    return res


def _fit_once(master, ranking, texts, skills_order) -> FitResult:
    owner = master.owner_of()
    ranking = [b for b in ranking if b in owner]
    # bullets the LLM forgot about still compete, at the bottom of the ranking
    ranking += [b.id for b in master.iter_bullets() if b.id not in ranking]

    required: list[str] = []
    for grp in list(master.roles) + list(master.projects):
        ids = [b for b in ranking if owner[b] == grp.id][: grp.min_bullets]
        required += ids
    optional = [b for b in ranking if b not in required]

    cache: dict[int, tuple[ResumeDoc, RenderResult]] = {}

    def attempt(k: int):
        if k not in cache:
            keep = set(required) | set(optional[:k])
            doc = build_doc(master, [b for b in ranking if b in keep], texts, skills_order)
            cache[k] = (doc, render(doc))
        return cache[k]

    if attempt(0)[1].pages > 1:
        raise ResumeDoesNotFit(
            "the required content alone is over one page; lower min_bullets or shorten bullets"
        )
    lo, hi = 0, len(optional)
    while lo < hi:  # largest k that still fits on one page
        mid = (lo + hi + 1) // 2
        if attempt(mid)[1].pages == 1:
            lo = mid
        else:
            hi = mid - 1
    doc, result = attempt(lo)
    keep = set(required) | set(optional[:lo])
    included = [b for b in ranking if b in keep]
    warnings = verify(doc, result)
    if result.fill < MIN_FILL and lo == len(optional):
        warnings.append(f"page only {result.fill:.0%} full (add more master bullets)")
    return FitResult(doc=doc, render=result, included=included, dropped=optional[lo:], warnings=warnings)


def save_pdf(result: RenderResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.pdf)
    return path


def render_text_pdf(title: str, body: str, path: Path) -> Path:
    """Plain one-page letter PDF (used for cover letters when a file is required)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        (tmpd / "d.json").write_text(json.dumps({"title": title, "body": body}))
        src = tmpd / "cl.typ"
        src.write_text(
            '#let d = json("d.json")\n'
            '#set page(paper: "us-letter", margin: 1in)\n'
            '#set text(font: "New Computer Modern", size: 11pt)\n'
            "#set par(spacing: 1em)\n"
            "#text(weight: \"bold\", d.title)\n\n"
            '#for p in d.body.split("\\n\\n") [#p\n\n]\n'
        )
        pdf = typst.compile(str(src))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    return path
