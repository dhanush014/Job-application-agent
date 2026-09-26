"""Render a ResumeDoc with the locked Typst template and fit it to exactly one full page.

The LLM supplies a *ranking* of bullets. This module decides the layout:

1. content: every role keeps its `min_bullets`, then the highest-ranked
   bullets are added at the 10pt baseline until one more would spill onto
   page two (binary search on the real rendered output);
2. type: the largest size up to 11pt that still fits and does not make any
   extra bullet wrap into a dangling one-word line (or wrap the header or
   skills lines);
3. spacing: gaps between bullets, entries and sections open up until the
   page is full (spacing never causes wrapping, so it is the safe filler);
4. finish: whatever sliver is left is spread evenly between sections.

If the required content does not fit at 10pt, type is scaled down (to 9.5pt)
before anything is declared impossible.
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import typst
from pypdf import PdfReader

from ..models import Contact, DocEntry, Layout, MasterResume, ResumeDoc, Segment, SkillLine

BASE_FONT = 1 / 3  # 10pt
MIN_FONT = 0.0  # 9.5pt
MAX_FONT = 1.0  # 11pt
FONT_STEP = 1 / 15  # 0.1pt
SKILL_LINE_CHARS = 100  # conservative one-line budget at 10pt
PAGE_HEIGHT_PT = 792.0
MARGIN_PT = 0.45 * 72
MIN_FILL = 0.85  # natural fill (before stretching) below this gets a warning
MAX_BOLD_PER_BULLET = 3
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
    header_ratio: float = 0.0
    skill_ratios: list[float] = field(default_factory=list)
    detail_ratios: list[float] = field(default_factory=list)  # education detail lines

    def dangling(self) -> list[int]:
        """Indexes (doc order) of bullets whose last wrapped line is tiny."""
        out = []
        for i, r in enumerate(self.line_ratios):
            frac = r - int(r)
            if r > 1 and 0 < frac < DANGLING_FRACTION:
                out.append(i)
        return out

    @property
    def lines_ok(self) -> bool:
        """One page, and the contact, skill and education detail lines don't wrap."""
        return (
            self.pages == 1
            and self.header_ratio <= 1
            and all(r <= 1 for r in self.skill_ratios + self.detail_ratios)
        )


@dataclass
class FitResult:
    doc: ResumeDoc
    render: RenderResult
    included: list[str]
    dropped: list[str]
    warnings: list[str] = field(default_factory=list)
    natural_fill: float = 1.0  # how full the page is before stretching


def _template() -> str:
    return resources.files("jobagent.resume").joinpath("template.typ").read_text()


class _Renderer:
    """Keeps one Typst compiler alive (fonts loaded once): ~5ms per render.
    Data goes in through sys.inputs so every compile sees the new content."""

    def __init__(self):
        self.lock = threading.Lock()
        self.dir = Path(tempfile.mkdtemp(prefix="jobagent-typst-"))
        (self.dir / "resume.typ").write_text(_template())  # static; content comes via sys.inputs
        self.compiler = typst.Compiler(str(self.dir / "resume.typ"))

    def compile(self, doc: ResumeDoc, **kw) -> bytes:
        return self.compiler.compile(sys_inputs={"data": doc.model_dump_json()}, **kw)

    def __call__(self, doc: ResumeDoc) -> RenderResult:
        with self.lock:
            pdf = self.compile(doc)
            marker = json.loads(self.compiler.query("<end>"))
            widths = json.loads(self.compiler.query("<widths>"))
        reader = PdfReader(io.BytesIO(pdf))
        pages = len(reader.pages)
        fill = 1.0
        if marker:
            pos = marker[-1]["value"]
            y = float(str(pos["y"]).removesuffix("pt"))
            usable = PAGE_HEIGHT_PT - 2 * MARGIN_PT
            fill = 1.0 if pos["page"] > 1 else max(0.0, min(1.0, (y - MARGIN_PT) / usable))
        w = widths[0]["value"] if widths else {}
        return RenderResult(
            pdf=pdf,
            pages=pages,
            fill=fill,
            text="\n".join(p.extract_text() or "" for p in reader.pages),
            line_ratios=[float(x) for x in w.get("bullets", [])],
            header_ratio=float(w.get("header", 0)),
            skill_ratios=[float(x) for x in w.get("skills", [])],
            detail_ratios=[float(x) for x in w.get("details", [])],
        )


_renderer: _Renderer | None = None


def _get_renderer() -> _Renderer:
    global _renderer
    if _renderer is None:
        _renderer = _Renderer()
    return _renderer


def render(doc: ResumeDoc) -> RenderResult:
    return _get_renderer()(doc)


def render_png(doc: ResumeDoc, ppi: int = 110) -> bytes:
    r = _get_renderer()
    with r.lock:
        return r.compile(doc, format="png", ppi=ppi)


def _font_pt(font: float) -> float:
    return 9.5 + 1.5 * font


def _skill_lines(master: MasterResume, order: list[str], font: float, bold: list[str] | None = None) -> list[SkillLine]:
    """One line per category. The job's skills (`bold`) go first and are bolded,
    then the tailored order; whatever doesn't fit on the line is dropped."""
    pos = {s.lower(): i for i, s in enumerate(order)}
    bold = [b for b in (bold or []) if b.strip()]

    def wanted(item: str) -> bool:
        return any(_find(t, item) for t in bold)

    budget_total = int(SKILL_LINE_CHARS * 10 / _font_pt(font))
    lines = []
    for cat, items in master.skills.items():
        ranked = sorted(items, key=lambda s: (not wanted(s), pos.get(s.lower(), 10_000), items.index(s)))
        budget = budget_total - len(cat) - 2
        kept: list[str] = []
        for s in ranked:
            if len(", ".join(kept + [s])) > budget:
                continue
            kept.append(s)
        segs: list[Segment] = []
        for i, item in enumerate(kept):
            if i:
                segs.append(Segment(t=", "))
            segs.append(Segment(t=item, b=wanted(item)))
        lines.append(SkillLine(category=cat, items=", ".join(kept), segments=segs if bold else []))
    return lines


def _find(term: str, text: str) -> re.Match | None:
    # short terms ("Go", "SQL") must match case exactly so the verb "go" stays plain
    flags = 0 if len(term) <= 3 else re.I
    # hyphens count as part of a word: "agent" is not bolded inside "multi-agent"
    return re.search(rf"(?<![\w+#-]){re.escape(term)}(?![\w+#-])", text, flags)


def bold_segments(text: str, terms: list[str], limit: int = MAX_BOLD_PER_BULLET) -> list[Segment]:
    """Split `text` into runs, bolding up to `limit` of `terms` (in priority order)."""
    spans: list[tuple[int, int]] = []
    for term in terms:
        if len(spans) >= limit:
            break
        m = _find(term, text) if term.strip() else None
        if m and not any(m.start() < e and s < m.end() for s, e in spans):
            spans.append((m.start(), m.end()))
    out, pos = [], 0
    for s, e in sorted(spans):
        if s > pos:
            out.append(Segment(t=text[pos:s]))
        out.append(Segment(t=text[s:e], b=True))
        pos = e
    if pos < len(text):
        out.append(Segment(t=text[pos:]))
    return out


def build_doc(
    master: MasterResume,
    chosen: list[str],
    texts: dict[str, str] | None = None,
    skills_order: list[str] | None = None,
    layout: Layout | None = None,
    bold_priority: list[str] | None = None,
    no_bold: set[str] | None = None,
) -> ResumeDoc:
    """`chosen` is in relevance order; bullets appear most-relevant-first per role.

    With `bold_priority` (the job description's tools and technologies, most
    important first), up to three of them are bolded in each bullet and the
    matching items are bolded and moved first in the Skills lines. Without a
    job, each bullet's own skill tags are bolded.
    """
    texts = texts or {}
    layout = layout or Layout()
    rank = {bid: i for i, bid in enumerate(chosen)}
    index = master.bullet_index()

    def terms_for(b) -> list[str]:
        if no_bold and b.id in no_bold:
            return []
        if bold_priority:  # tailoring: bold the job description's tools wherever they appear
            return list(bold_priority)
        return list(b.skills)  # no job: bold the bullet's own skill tags

    def entry_bullets(bs):
        picked = sorted((b for b in bs if b.id in rank), key=lambda b: rank[b.id])
        plain = [texts.get(b.id, index[b.id].text) for b in picked]
        segs = [bold_segments(t, terms_for(b)) for t, b in zip(plain, picked)]
        return plain, segs, [b.id for b in picked]

    def job_entries(roles) -> list[DocEntry]:
        out = []
        for r in roles:
            plain, segs, ids = entry_bullets(r.bullets)
            out.append(DocEntry(
                heading=r.company, subheading=r.title, location=r.location,
                dates=f"{r.start} – {r.end}", bullets=plain, segments=segs, ids=ids,
            ))
        return out

    roles = job_entries(master.roles)
    academic = [e for e in job_entries(master.academic) if e.bullets]
    projects = []
    for p in master.projects:
        plain, segs, ids = entry_bullets(p.bullets)
        projects.append(DocEntry(
            heading=p.name, subheading=p.role or p.link, location=p.location, dates=p.dates,
            bullets=plain, segments=segs, ids=ids,
        ))
    projects = [p for p in projects if p.bullets]
    return ResumeDoc(
        contact=master.contact,
        education=master.education,
        roles=roles,
        academic=academic,
        projects=projects,
        skills=_skill_lines(master, skills_order or [], layout.font, bold_priority),
        layout=layout,
    )


def doc_bullets(doc: ResumeDoc) -> list[str]:
    return [b for e in doc.entries() for b in e.bullets]


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
    for b in doc_bullets(doc):
        probe = _norm(b)[:30]
        if probe and probe not in text:
            problems.append(f"bullet not extractable from PDF: {b[:40]}…")
    return problems


def fit(
    master: MasterResume,
    ranking: list[str],
    texts: dict[str, str] | None = None,
    skills_order: list[str] | None = None,
    bold_priority: list[str] | None = None,
    relevance: dict[str, int] | None = None,
    min_relevance: int = 0,
) -> FitResult:
    """Fit to one full page.

    Bullets scoring under `min_relevance` for this job are left out (the space
    goes to spacing instead), unless leaving them out would make the page look
    sparse; then they come back in rank order. Each role's `min_bullets` always
    stay. When a bullet ends in a dangling last line, an LLM rewrite is first
    reverted to the master wording, then that bullet's bold is dropped (bold is
    wider), and the page is re-fitted. Leftovers are master bullets to shorten."""
    weak: set[str] = set()
    if relevance is not None and min_relevance > 0:
        weak = {b.id for b in master.iter_bullets() if relevance.get(b.id, 0) < min_relevance}
    res = _fit_polished(master, ranking, texts, skills_order, bold_priority, weak)
    if weak and res.natural_fill < MIN_FILL:
        res = _fit_polished(master, ranking, texts, skills_order, bold_priority, set())
        res.warnings.append("few bullets matched this job strongly; added lower-relevance ones to fill the page")
    return res


def _fit_polished(master, ranking, texts, skills_order, bold_priority, exclude) -> FitResult:
    texts = dict(texts or {})
    index = master.bullet_index()
    no_bold: set[str] = set()
    reverted = 0
    for _ in range(4):
        res = _fit_once(master, ranking, texts, skills_order, bold_priority, no_bold, exclude)
        ids = doc_ids(res.doc)
        segs = [s for e in res.doc.entries() for s in e.segments]
        changed = False
        for i in res.render.dangling():
            bid = ids[i]
            if bid in texts and texts[bid] != index[bid].text:
                texts.pop(bid)
                reverted += 1
                changed = True
            elif bid not in no_bold and any(x.b for x in segs[i]):
                no_bold.add(bid)
                changed = True
        if not changed:
            break
    if reverted:
        res.warnings.append(f"reverted {reverted} rewrite(s) that left a dangling line")
    bullets = doc_bullets(res.doc)
    for i in res.render.dangling():
        res.warnings.append(f"dangling last line (shorten this master bullet): {bullets[i][:60]}…")
    return res


def doc_ids(doc: ResumeDoc) -> list[str]:
    return [i for e in doc.entries() for i in e.ids]


def _fit_once(master, ranking, texts, skills_order, bold_priority, no_bold, exclude=frozenset()) -> FitResult:
    owner = master.owner_of()
    ranking = [b for b in ranking if b in owner]
    # bullets the LLM forgot about still compete, at the bottom of the ranking
    ranking += [b.id for b in master.iter_bullets() if b.id not in ranking]

    required: list[str] = []
    capped: set[str] = set()  # bullets past a role's max_bullets never compete
    for grp in master.groups():
        mine = [b for b in ranking if owner[b] == grp.id]
        required += mine[: grp.min_bullets]
        if grp.max_bullets is not None:
            capped |= set(mine[grp.max_bullets:])
    optional = [b for b in ranking if b not in required and b not in capped and b not in exclude]

    cache: dict[tuple, tuple[ResumeDoc, RenderResult]] = {}

    def attempt(n: int, font: float, spacing: float = 0.0, stretch: bool = False):
        key = (n, round(font, 4), round(spacing, 4), stretch)
        if key not in cache:
            keep = set(required) | set(optional[:n])
            doc = build_doc(master, [b for b in ranking if b in keep], texts, skills_order,
                            Layout(font=font, spacing=spacing, stretch=stretch), bold_priority, no_bold)
            cache[key] = (doc, render(doc))
        return cache[key]

    # 1. content: most bullets that fit at the baseline (or tighter, if needed)
    base = BASE_FONT
    if not attempt(0, base)[1].lines_ok:
        base = MIN_FONT
        if not attempt(0, base)[1].lines_ok:
            raise ResumeDoesNotFit(
                "the required content is over one page even at 9.5pt; lower min_bullets or shorten bullets"
            )
    lo, hi = 0, len(optional)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if attempt(mid, base)[1].lines_ok:
            lo = mid
        else:
            hi = mid - 1
    n = lo

    # 2. type: largest size that fits without adding dangling lines
    allowed = len(attempt(n, base)[1].dangling())
    font = base
    f = MAX_FONT
    while f > base + 1e-9:
        r = attempt(n, f)[1]
        if r.lines_ok and len(r.dangling()) <= allowed:
            font = f
            break
        f -= FONT_STEP

    # 3. spacing: open up gaps until the page is full
    if attempt(n, font, 1.0)[1].lines_ok:
        spacing = 1.0
    else:
        s_lo, s_hi = 0.0, 1.0
        for _ in range(8):
            mid = (s_lo + s_hi) / 2
            if attempt(n, font, mid)[1].lines_ok:
                s_lo = mid
            else:
                s_hi = mid
        spacing = s_lo

    # 4. finish: spread the remaining sliver between sections. Only a sliver:
    # stretching a thin resume makes huge gaps, which looks worse than space
    # at the bottom (the fix there is more master bullets).
    doc, natural = attempt(n, font, spacing)
    result = natural
    if natural.fill >= MIN_FILL:
        stretched_doc, stretched = attempt(n, font, spacing, stretch=True)
        if stretched.pages == 1:  # never trade the one-page guarantee for looks
            doc, result = stretched_doc, stretched

    keep = set(required) | set(optional[:n])
    included = [b for b in ranking if b in keep]
    warnings = verify(doc, result)
    if natural.fill < MIN_FILL and n == len(optional):
        warnings.append(
            f"only {natural.fill:.0%} of the page is used even with generous spacing; "
            "add more bullets to your master resume"
        )
    return FitResult(
        doc=doc, render=result, included=included, dropped=optional[n:],
        warnings=warnings, natural_fill=natural.fill,
    )


def save_pdf(result: RenderResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.pdf)
    return path


def render_cover_letter(contact: Contact, title: str, company: str, body: str, path: Path) -> Path:
    """One-page cover letter PDF that matches the resume header."""
    from datetime import date

    paragraphs = [
        [" ".join(line.split()) for line in p.splitlines() if line.strip()]
        for p in re.split(r"\n\s*\n", body.strip())
        if p.strip()
    ]
    data = {
        "contact": contact.model_dump(mode="json"),
        "title": title,
        "company": company,
        "date": date.today().strftime("%B %-d, %Y"),
        "paragraphs": paragraphs,
    }
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "cover.typ"
        src.write_text(resources.files("jobagent.resume").joinpath("cover.typ").read_text())
        pdf = typst.compile(str(src), sys_inputs={"data": json.dumps(data)})
    if len(PdfReader(io.BytesIO(pdf)).pages) != 1:
        raise ValueError("cover letter is longer than one page")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    return path
