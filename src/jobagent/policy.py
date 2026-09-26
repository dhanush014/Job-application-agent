"""Terms / policy checks on job postings and form questions.

Anything flagged here is prepared as usual but routed to the review queue
instead of being auto-submitted.
"""

from __future__ import annotations

import re

from .models import FormField, Job

# Employers that explicitly forbid automated or AI-written applications.
AUTOMATION_BAN = [
    r"\bno (?:ai|automated|bot)[- ](?:generated |assisted )?(?:applications|submissions)",
    r"\bdo not (?:use|submit) (?:ai|chatgpt|automated tools|bots)",
    r"\bapplications? (?:submitted|generated|written) (?:by|with|using) (?:ai|bots?|automated tools)[^.]{0,60}(?:rejected|disqualified|not be considered)",
    r"\b(?:ai|llm)[- ]generated (?:applications|answers|responses|cover letters)[^.]{0,40}(?:rejected|disqualified|not be considered|not accepted)",
    r"\bwe (?:will )?(?:reject|disqualify) (?:any )?(?:ai|automated)",
]

# Canary / prompt-injection text aimed at LLM applicants.
HONEYPOT = [
    r"\bif you are an? (?:ai|llm|language model|bot|automated)",
    r"\b(?:ai|llms?|chatgpt|language models?|bots?) (?:should|must|need to) (?:include|mention|write|add|start)",
    r"\bignore (?:all |any )?(?:previous|prior|above) instructions",
    r"\binclude the (?:word|phrase|code) [\"'“]",
]

_BAN = [re.compile(p, re.I) for p in AUTOMATION_BAN]
_POT = [re.compile(p, re.I) for p in HONEYPOT]


def check_job(job: Job) -> list[str]:
    reasons = []
    text = f"{job.title}\n{job.description}"
    for p in _BAN:
        if m := p.search(text):
            reasons.append(f"posting restricts automated/AI applications: “{m.group(0)}”")
            break
    for p in _POT:
        if m := p.search(text):
            reasons.append(f"posting contains text aimed at AI applicants: “{m.group(0)}”")
            break
    return reasons


def check_fields(fields: list[FormField]) -> list[str]:
    reasons = []
    for f in fields:
        text = f"{f.label}\n{f.description}"
        if any(p.search(text) for p in _POT + _BAN):
            reasons.append(f"question aimed at AI applicants: “{f.label[:80]}”")
    return reasons
