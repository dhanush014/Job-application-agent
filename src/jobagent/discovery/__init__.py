"""Job discovery via the public Greenhouse and Ashby job board APIs."""

from __future__ import annotations

import logging

import httpx

from ..config import Company
from ..models import FormField, Job
from . import ashby, greenhouse

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


def make_client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def fetch_jobs(client: httpx.Client, company: Company) -> list[Job]:
    mod = greenhouse if company.ats == "greenhouse" else ashby
    return mod.fetch_jobs(client, company.slug, company.name or company.slug)


def fetch_all(client: httpx.Client, companies: list[Company]) -> list[Job]:
    jobs: list[Job] = []
    for c in companies:
        try:
            jobs += fetch_jobs(client, c)
        except Exception as e:  # one broken board must not stop the run
            log.warning("discovery failed for %s/%s: %s", c.ats, c.slug, e)
    return jobs


def fetch_form(client: httpx.Client, job: Job) -> list[FormField]:
    mod = greenhouse if job.ats == "greenhouse" else ashby
    return mod.fetch_form(client, job.company, job.job_id)
