"""LLM tailoring + the validator that stops it from inventing experience."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import LLM, untrusted
from .models import MasterResume, TailorOutput

SYSTEM = """You tailor a resume to a job description.
You receive the candidate's master bullets (each with an id) and a job description.
Return EVERY bullet id exactly once, ranked by relevance to the job (0-100).
You may lightly rephrase a bullet to mirror the job's terminology, but:
- never add a tool, skill, technology, number, employer, title or claim that is not in that bullet
- never change or add numbers or metrics
- keep it one sentence, past tense, under 170 characters
- if unsure, return the original text unchanged
Also return the candidate's skills (only from the provided skill list) ordered by relevance."""


@dataclass
class Tailoring:
    ranking: list[str]
    texts: dict[str, str]  # only bullets whose rewrite passed validation
    skills: list[str]
    rejected: list[str] = field(default_factory=list)  # human-readable reasons
    relevance: dict[str, int] = field(default_factory=dict)  # LLM's 0-100 score per bullet id


def tailor(llm: LLM, master: MasterResume, jd: str, jd_keywords: list[str], max_jd_chars: int = 7000) -> Tailoring:
    owner = master.owner_of()
    groups = {r.id: f"{r.title} @ {r.company}" for r in master.roles}
    groups.update({p.id: f"Project: {p.name}" for p in master.projects})
    lines = [f"[{b.id}] ({groups[owner[b.id]]}) {b.text}" for b in master.iter_bullets()]
    user = (
        "MASTER BULLETS:\n"
        + "\n".join(lines)
        + "\n\nSKILLS: "
        + ", ".join(master.all_skills())
        + "\n\n"
        + untrusted("job_description", jd[:max_jd_chars])
    )
    out = llm.json("smart", SYSTEM, user, TailorOutput)
    return validate(master, out, jd_keywords)


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(s)}


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w+#]){re.escape(word.lower())}(?![\w+#])", text.lower()) is not None


def validate(master: MasterResume, out: TailorOutput, jd_keywords: list[str]) -> Tailoring:
    index = master.bullet_index()
    ranked = sorted(out.bullets, key=lambda b: -b.relevance)
    relevance: dict[str, int] = {}
    for tb in ranked:
        if tb.source_id in index:
            relevance.setdefault(tb.source_id, tb.relevance)
    ranking: list[str] = []
    texts: dict[str, str] = {}
    rejected: list[str] = []
    for tb in ranked:
        src = index.get(tb.source_id)
        if src is None or tb.source_id in ranking:
            continue
        ranking.append(tb.source_id)
        new = " ".join(tb.text.split())
        if not new or new == src.text:
            continue
        reason = _check_rewrite(src.text, src.skills, new, jd_keywords)
        if reason:
            rejected.append(f"{tb.source_id}: {reason}")
        else:
            texts[tb.source_id] = new
    # never lose a bullet because the LLM skipped it
    ranking += [b.id for b in master.iter_bullets() if b.id not in ranking]

    allowed = {s.lower(): s for s in master.all_skills()}
    skills: list[str] = []
    for s in out.skills:
        canon = allowed.get(s.strip().lower())
        if canon and canon not in skills:
            skills.append(canon)
    return Tailoring(ranking=ranking, texts=texts, skills=skills, rejected=rejected, relevance=relevance)


def _check_rewrite(src: str, src_skills: list[str], new: str, jd_keywords: list[str]) -> str | None:
    if len(new) < 0.5 * len(src):
        return "rewrite dropped too much content"
    extra = _numbers(new) - _numbers(src)
    if extra:
        return f"invented numbers {sorted(extra)}"
    backing = src + " " + " ".join(src_skills)
    for kw in jd_keywords:
        kw = kw.strip()
        if len(kw) < 2:
            continue
        if _has_word(new, kw) and not _has_word(backing, kw):
            return f"added job keyword not backed by this bullet: {kw!r}"
    if re.search(r"[\[\]{}<>]|lorem|as an ai", new, re.I):
        return "placeholder or markup in rewrite"
    return None
