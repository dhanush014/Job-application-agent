"""Answer application form questions.

Order of precedence for every field:
  1. files (resume / cover letter)
  2. profile fields (name, email, links, ...)
  3. your answer bank (answers.yaml), fuzzy-matched on the question label
  4. the LLM, but ONLY for non-sensitive questions
Sensitive questions (work authorization, sponsorship, salary, EEO, criminal
history, ...) are never guessed: without a bank match they are left for you
to answer in the review queue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

from .llm import LLM, untrusted
from .models import Answer, FieldType, FormField, MasterResume, Option

PROFILE_FIELDS: list[tuple[str, list[str], list[str]]] = [
    # (profile key, exact field names, label regexes)
    ("first_name", ["first_name", "_systemfield_first_name"], [r"^(legal )?first name", r"^given name"]),
    ("last_name", ["last_name", "_systemfield_last_name"], [r"^(legal )?last name", r"^surname", r"^family name"]),
    ("preferred_name", ["preferred_name"], [r"^preferred (first )?name"]),
    ("full_name", ["_systemfield_name"], [r"^(full |legal )?name$", r"^full name"]),
    ("email", ["email", "_systemfield_email"], [r"^e-?mail"]),
    ("phone", ["phone", "_systemfield_phone"], [r"^(phone|mobile|cell)"]),
    ("linkedin", [], [r"linkedin"]),
    ("github", [], [r"github"]),
    ("website", [], [r"^(personal )?(website|portfolio|blog)", r"^other website"]),
    ("location", ["_systemfield_location", "location"], [r"^(current )?location", r"^city", r"^where are you (currently )?(located|based)"]),
    ("current_company", [], [r"^current (company|employer)", r"^(most recent|current or most recent) (company|employer)"]),
    ("current_title", [], [r"^current (job )?title", r"^current role"]),
]

SENSITIVE = [
    "sponsor", "visa", "authoriz", "legally", "eligible to work", "right to work", "salary",
    "compensation", "pay expectation", "desired pay", "criminal", "convict", "felony",
    "background check", "drug", "gender", "race", "ethnic", "hispanic", "latin",
    "veteran", "disab", "pronoun", "sexual orientation", "transgender", "date of birth",
    "your age", "relocat", "clearance", "citizen", "export control", "itar", "notice period",
    "start date", "non-compete", "non-solicit", "previously worked", "previously employed",
    "former employee", "relative", "related to", "government official",
]

PLACEHOLDER = re.compile(r"\[[^\]]*\]|\{[^}]*\}|<[^>]+>|lorem ipsum|as an ai|language model|insert ", re.I)


def is_sensitive(f: FormField) -> bool:
    text = f"{f.label} {f.description}".lower()
    return f.eeo or any(k in text for k in SENSITIVE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


_MONEY = r"\$\s?\d{2,3}(?:,\d{3})*(?:\.\d+)?\s?[kK]?"
_RANGE = re.compile(rf"({_MONEY})\s*(?:-|–|—|to)\s*({_MONEY})")


def posted_salary(jd: str) -> str | None:
    """The salary range stated in a job description, e.g. '$140,000 – $180,000'."""
    for lo, hi in _RANGE.findall(jd or ""):
        vals = [_dollars(lo), _dollars(hi)]
        if all(v and 30_000 <= v <= 1_000_000 for v in vals) and vals[0] <= vals[1]:
            return f"${vals[0]:,} – ${vals[1]:,}"
    return None


def _dollars(s: str) -> int | None:
    s = s.replace("$", "").replace(",", "").strip()
    mult = 1000 if s[-1:] in "kK" else 1
    try:
        return int(float(s.rstrip("kK").strip()) * mult)
    except ValueError:
        return None


def pick_option(answer: str, options: list[Option]) -> Option | None:
    if not options:
        return None
    a = _norm(answer)
    for o in options:
        if _norm(o.label) == a:
            return o
    if a in ("yes", "y", "true"):
        return next((o for o in options if _norm(o.label).startswith("yes")), None)
    if a in ("no", "n", "false"):
        return next((o for o in options if _norm(o.label).startswith("no")), None)
    for o in options:
        ol = _norm(o.label)
        if ol and (ol.startswith(a) or a.startswith(ol)):
            return o
    best = process.extractOne(a, [_norm(o.label) for o in options], scorer=fuzz.token_set_ratio)
    if best and best[1] >= 88:
        return options[best[2]]
    return None


@dataclass
class Bank:
    profile: dict
    questions: list[dict]

    @classmethod
    def from_yaml(cls, data: dict) -> "Bank":
        profile = dict(data.get("profile") or {})
        if "full_name" not in profile and profile.get("first_name"):
            profile["full_name"] = f"{profile['first_name']} {profile.get('last_name', '')}".strip()
        return cls(profile=profile, questions=list(data.get("questions") or []))

    def profile_value(self, f: FormField) -> str | None:
        if f.type not in (FieldType.TEXT, FieldType.TEXTAREA):
            return None
        label = _norm(f.label)
        for key, names, patterns in PROFILE_FIELDS:
            if f.name in names or (
                len(label.split()) <= 7 and any(re.search(p, label) for p in patterns)
            ):
                v = self.profile.get(key)
                if v:
                    return str(v)
        return None

    def match(self, f: FormField) -> tuple[dict | None, int]:
        label = _norm(f"{f.label}")
        best, best_score = None, 0
        for q in self.questions:
            for pat in q.get("match", []):
                p = _norm(pat)
                if not p:
                    continue
                if re.search(rf"\b{re.escape(p)}\b", label):
                    score = 100
                elif len(p) >= 12:  # fuzzy only for long phrases; short ones must match exactly
                    score = int(fuzz.partial_ratio(p, label))
                else:
                    score = 0
                if score > best_score:
                    best, best_score = q, score
        return (best, best_score) if best_score >= 90 else (None, best_score)


class _Choice(BaseModel):
    field: str
    choices: list[str] = Field(description="option label(s) exactly as given")
    confidence: int = Field(ge=0, le=100)


class ChoiceOutput(BaseModel):
    answers: list[_Choice]


class _Written(BaseModel):
    field: str
    text: str
    confidence: int = Field(ge=0, le=100)


class WrittenOutput(BaseModel):
    answers: list[_Written]


CHOICE_SYSTEM = """You fill out a job application for the candidate. For each question pick the
option(s) that truthfully describe the candidate based ONLY on their profile. If the profile
does not tell you, give confidence below 50. Use option labels exactly as given."""

WRITE_SYSTEM = """You write answers to job application questions for the candidate, in first person.
Rules: only use facts from the candidate profile; never invent experience, numbers or employers;
be specific, warm and concise (short text fields: under 20 words; long answers: 60-150 words);
no placeholders, no brackets, no mention of being an AI. If a question cannot be answered from
the profile, give confidence below 50."""


def _profile_text(master: MasterResume, bank: Bank) -> str:
    lines = [f"Name: {master.contact.name}", f"Location: {master.contact.location}"]
    for r in master.roles + master.academic:
        lines.append(f"{r.title} at {r.company} ({r.start} - {r.end})")
        lines += [f"  - {b.text}" for b in r.bullets]
    for p in master.projects:
        lines.append(f"Project {p.name}: " + " ".join(b.text for b in p.bullets))
    lines.append("Skills: " + ", ".join(master.all_skills()))
    lines += [f"Education: {e.degree}, {e.school} ({e.end})" for e in master.education]
    for q in bank.questions:
        if q.get("answer") and not q.get("private"):
            lines.append(f"Q: {q['match'][0]} -> {q['answer']}")
    return "\n".join(lines)


def _is_resume(f: FormField) -> bool:
    return "resume" in f.name.lower() or re.search(r"\b(resume|cv|résumé)\b", f.label, re.I) is not None


def _is_cover(f: FormField | Answer) -> bool:
    name = f.name if isinstance(f, FormField) else f.field
    return "cover" in name.lower() or "cover letter" in f.label.lower()


def is_cover_answer(a: Answer) -> bool:
    return _is_cover(a)


def answer_form(
    fields: list[FormField],
    bank: Bank,
    master: MasterResume,
    llm: LLM,
    job_title: str,
    company: str,
    jd: str,
    cover_policy: str = "when_asked",
) -> tuple[list[Answer], bool]:
    """Returns (answers, needs_cover_letter)."""
    answers: list[Answer] = []
    need_choice: list[tuple[int, FormField]] = []
    need_text: list[tuple[int, FormField]] = []
    needs_cover = False

    for f in fields:
        a = Answer(
            field=f.name, label=f.label, type=f.type, required=f.required,
            options=f.options, sensitive=is_sensitive(f),
        )
        answers.append(a)
        i = len(answers) - 1

        if f.type == FieldType.FILE:
            if _is_resume(f):
                a.value, a.source, a.confidence = "resume", "file", 100
            elif _is_cover(f) and (f.required or cover_policy == "when_asked"):
                a.value, a.source, a.confidence = "cover_letter", "file", 100
                needs_cover = True
            elif f.required:
                a.note = "required file upload the agent cannot provide"
            continue

        if f.type == FieldType.UNKNOWN:
            if f.required:
                a.note = "unsupported field type"
            continue

        if (v := bank.profile_value(f)) is not None:
            a.value, a.source, a.confidence = v, "profile", 100
            continue

        if _is_cover(f) and f.type == FieldType.TEXTAREA:
            if f.required or cover_policy == "when_asked":
                a.source, a.note = "llm", "cover letter"
                needs_cover = True
            continue

        entry, score = bank.match(f)
        if entry is not None:
            answer = entry["answer"]
            if entry.get("use_posted_salary") and (posted := posted_salary(jd)):
                answer = posted  # the posting's own range beats your default number
            _apply_bank(a, f, answer, score)
            continue

        if a.sensitive:
            if f.required:
                a.note = "sensitive question — add it to answers.yaml or answer here"
            continue

        if f.type in (FieldType.SELECT, FieldType.MULTISELECT, FieldType.BOOLEAN):
            if f.required:
                need_choice.append((i, f))
        elif f.required:
            need_text.append((i, f))

    profile = _profile_text(master, bank)
    if need_choice:
        _llm_choices(llm, answers, need_choice, profile, job_title, company)
    if need_text:
        _llm_written(llm, answers, need_text, profile, job_title, company, jd)
    return answers, needs_cover


def _options_for(f: FormField) -> list[Option]:
    if f.type == FieldType.BOOLEAN and not f.options:
        return [Option(label="Yes", value="true"), Option(label="No", value="false")]
    return f.options


def _apply_bank(a: Answer, f: FormField, value, score: int) -> None:
    a.source, a.confidence = "bank", score
    opts = _options_for(f)
    if f.type == FieldType.MULTISELECT:
        vals = value if isinstance(value, list) else [value]
        picked = [o.label for v in vals if (o := pick_option(str(v), opts))]
        if picked:
            a.value = picked
        else:
            a.confidence, a.note = 0, f"bank answer {value!r} matches no option"
    elif opts:
        o = pick_option(str(value), opts)
        if o:
            a.value = o.label
        else:
            a.confidence, a.note = 0, f"bank answer {value!r} matches no option"
    else:
        a.value = str(value)


def _llm_choices(llm, answers, items, profile, job_title, company) -> None:
    qs = []
    for _, f in items:
        opts = " | ".join(o.label for o in _options_for(f))
        multi = " (choose one or more)" if f.type == FieldType.MULTISELECT else " (choose one)"
        qs.append(f"- field={f.name}: {f.label}{multi}\n  options: {opts}")
    user = (
        f"CANDIDATE PROFILE:\n{profile}\n\nApplying to: {job_title} at {company}\n\n"
        + untrusted("questions", "\n".join(qs))
    )
    out = llm.json("fast", CHOICE_SYSTEM, user, ChoiceOutput)
    got = {c.field: c for c in out.answers}
    for i, f in items:
        a, c = answers[i], got.get(f.name)
        a.source = "llm_choice"
        if not c:
            a.note = "LLM gave no answer"
            continue
        opts = _options_for(f)
        picked = [o.label for ch in c.choices if (o := pick_option(ch, opts))]
        if not picked:
            a.note = f"LLM choice {c.choices!r} matches no option"
            continue
        a.value = picked if f.type == FieldType.MULTISELECT else picked[0]
        a.confidence = c.confidence


def _llm_written(llm, answers, items, profile, job_title, company, jd) -> None:
    qs = [f"- field={f.name} ({'long answer' if f.type == FieldType.TEXTAREA else 'short answer'}): {f.label}"
          + (f"\n  ({f.description[:300]})" if f.description else "") for _, f in items]
    user = (
        f"CANDIDATE PROFILE:\n{profile}\n\nApplying to: {job_title} at {company}\n\n"
        + untrusted("job_description", jd[:5000])
        + "\n\n"
        + untrusted("questions", "\n".join(qs))
    )
    out = llm.json("smart", WRITE_SYSTEM, user, WrittenOutput)
    got = {w.field: w for w in out.answers}
    for i, f in items:
        a, w = answers[i], got.get(f.name)
        a.source = "llm"
        if not w:
            a.note = "LLM gave no answer"
            continue
        text = w.text.strip()
        limit = 2000 if f.type == FieldType.TEXTAREA else 250
        if not text or len(text) > limit or PLACEHOLDER.search(text):
            a.note = "LLM answer failed checks (empty, too long, or placeholder text)"
            continue
        a.value, a.confidence = text, w.confidence


class CoverLetter(BaseModel):
    text: str = Field(min_length=200, max_length=2500)


COVER_SYSTEM = """Write a concise cover letter (180-260 words, 3 short paragraphs, first person) for the
candidate. Only use facts from the profile and the tailored bullets. No placeholders, no address
block, no date. Start with "Dear Hiring Team,", separate paragraphs with a blank line, and end with
"Sincerely," followed by a newline and the candidate's name."""


def write_cover_letter(llm: LLM, master: MasterResume, bullets: list[str], job_title: str, company: str, jd: str) -> str | None:
    user = (
        f"CANDIDATE: {master.contact.name}\nTAILORED BULLETS:\n" + "\n".join(f"- {b}" for b in bullets)
        + f"\n\nROLE: {job_title} at {company}\n\n" + untrusted("job_description", jd[:5000])
    )
    try:
        text = llm.json("smart", COVER_SYSTEM, user, CoverLetter).text.strip()
    except ValueError:
        return None
    return None if PLACEHOLDER.search(text) else text


def review_reasons(answers: list[Answer], min_confidence: int) -> list[str]:
    reasons = []
    for a in answers:
        if not a.required:
            continue
        if a.value in (None, "", []):
            reasons.append(f"Needs your answer: “{a.label}”" + (f" ({a.note})" if a.note else ""))
        elif a.source in ("llm", "llm_choice", "bank") and a.confidence < min_confidence:
            reasons.append(f"Low-confidence answer ({a.confidence}): “{a.label}”")
    return reasons
