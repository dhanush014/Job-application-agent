"""Ashby public posting API (read-only, no auth) + hosted form schema.

Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
The application form schema comes from the same GraphQL endpoint the hosted
jobs page uses; if Ashby changes it, forms fall back to the review queue.
"""

from __future__ import annotations

import httpx

from ..models import FieldType, FormField, Job, Option
from .html import html_to_text

API = "https://api.ashbyhq.com/posting-api/job-board"
GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"

_TYPES = {
    "String": FieldType.TEXT,
    "Email": FieldType.TEXT,
    "Phone": FieldType.TEXT,
    "Number": FieldType.TEXT,
    "SocialLink": FieldType.TEXT,
    "Location": FieldType.TEXT,
    "Date": FieldType.TEXT,
    "LongText": FieldType.TEXTAREA,
    "File": FieldType.FILE,
    "ValueSelect": FieldType.SELECT,
    "MultiValueSelect": FieldType.MULTISELECT,
    "Boolean": FieldType.BOOLEAN,
}

_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title
    applicationForm { sections { title fieldEntries { ... on FormFieldEntry { id field isRequired descriptionHtml } } } }
  }
}"""


def apply_url(org: str, job_id: str) -> str:
    return f"https://jobs.ashbyhq.com/{org}/{job_id}/application"


def fetch_jobs(client: httpx.Client, org: str, name: str) -> list[Job]:
    r = client.get(f"{API}/{org}", params={"includeCompensation": "true"})
    r.raise_for_status()
    return parse_jobs(r.json(), org, name)


def parse_jobs(data: dict, org: str, name: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        jid = str(j["id"])
        jobs.append(
            Job(
                ats="ashby",
                company=org,
                company_name=name,
                job_id=jid,
                title=j.get("title", "").strip(),
                location=j.get("location") or "",
                url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{org}/{jid}",
                apply_url=j.get("applyUrl") or apply_url(org, jid),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                posted_at=j.get("publishedAt"),
                remote=j.get("isRemote"),
                employment_type=j.get("employmentType"),
            )
        )
    return jobs


def fetch_form(client: httpx.Client, org: str, job_id: str) -> list[FormField]:
    r = client.post(
        GRAPHQL,
        json={
            "operationName": "ApiJobPosting",
            "variables": {"organizationHostedJobsPageName": org, "jobPostingId": job_id},
            "query": _QUERY,
        },
    )
    r.raise_for_status()
    return parse_form(r.json())


def parse_form(data: dict) -> list[FormField]:
    posting = ((data or {}).get("data") or {}).get("jobPosting")
    if not posting or not posting.get("applicationForm"):
        raise ValueError("Ashby form schema not found")
    out = []
    for section in posting["applicationForm"].get("sections", []):
        for entry in section.get("fieldEntries", []):
            f = entry.get("field") or {}
            if not f.get("path"):
                continue
            opts = f.get("selectableValues") or []
            out.append(
                FormField(
                    name=f["path"],
                    label=(f.get("title") or "").strip(),
                    type=_TYPES.get(f.get("type"), FieldType.UNKNOWN),
                    required=bool(entry.get("isRequired")),
                    options=[Option(label=str(o.get("label")), value=str(o.get("value"))) for o in opts],
                    description=html_to_text(entry.get("descriptionHtml")),
                )
            )
    return out
