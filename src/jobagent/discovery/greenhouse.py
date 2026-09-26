"""Greenhouse public Job Board API (read-only, no auth).

Docs: https://developers.greenhouse.io/job-board.html
"""

from __future__ import annotations

import httpx

from ..models import FieldType, FormField, Job, Option
from .html import html_to_text

API = "https://boards-api.greenhouse.io/v1/boards"

_TYPES = {
    "input_text": FieldType.TEXT,
    "textarea": FieldType.TEXTAREA,
    "input_file": FieldType.FILE,
    "multi_value_single_select": FieldType.SELECT,
    "multi_value_multi_select": FieldType.MULTISELECT,
}


def apply_url(slug: str, job_id: str) -> str:
    return f"https://job-boards.greenhouse.io/{slug}/jobs/{job_id}"


def fetch_jobs(client: httpx.Client, slug: str, name: str) -> list[Job]:
    r = client.get(f"{API}/{slug}/jobs", params={"content": "true"})
    r.raise_for_status()
    return parse_jobs(r.json(), slug, name)


def parse_jobs(data: dict, slug: str, name: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        jid = str(j["id"])
        loc = (j.get("location") or {}).get("name", "") or ""
        jobs.append(
            Job(
                ats="greenhouse",
                company=slug,
                company_name=j.get("company_name") or name,
                job_id=jid,
                title=j.get("title", "").strip(),
                location=loc,
                url=j.get("absolute_url") or apply_url(slug, jid),
                apply_url=apply_url(slug, jid),
                description=html_to_text(j.get("content")),
                posted_at=j.get("first_published") or j.get("updated_at"),
                remote="remote" in loc.lower(),
            )
        )
    return jobs


def fetch_form(client: httpx.Client, slug: str, job_id: str) -> list[FormField]:
    r = client.get(f"{API}/{slug}/jobs/{job_id}", params={"questions": "true"})
    r.raise_for_status()
    return parse_form(r.json())


def _parse_questions(questions: list[dict], eeo: bool = False) -> list[FormField]:
    out = []
    for q in questions or []:
        fields = [f for f in q.get("fields", []) if f.get("type") != "input_hidden"]
        if not fields:
            continue
        # A question can offer alternatives (e.g. resume file OR pasted text);
        # the first field is the canonical one.
        f = fields[0]
        out.append(
            FormField(
                name=f["name"],
                label=(q.get("label") or "").strip(),
                type=_TYPES.get(f.get("type"), FieldType.UNKNOWN),
                required=bool(q.get("required")),
                options=[Option(label=str(v["label"]), value=str(v["value"])) for v in f.get("values", []) or []],
                description=html_to_text(q.get("description")),
                eeo=eeo,
            )
        )
    return out


def parse_form(data: dict) -> list[FormField]:
    fields = _parse_questions(data.get("questions", []))
    fields += _parse_questions(data.get("location_questions", []))
    for block in data.get("compliance") or []:
        fields += _parse_questions(block.get("questions", []), eeo=True)
    return fields
