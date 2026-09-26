"""Job discovery via the public Greenhouse and Ashby job board APIs."""

from __future__ import annotations

import logging
import re

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


_GREENHOUSE_URL = re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)(?:/jobs/|&token=)(\d+)")
_GREENHOUSE_EMBED = re.compile(r"greenhouse\.io/embed/job_app\?.*?for=([\w-]+).*?token=(\d+)")
_ASHBY_URL = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-fA-F-]{36})")


def job_from_url(client: httpx.Client, url: str) -> Job:
    """Look up one posting from its Greenhouse or Ashby link."""
    if m := (_GREENHOUSE_EMBED.search(url) or _GREENHOUSE_URL.search(url)):
        slug, job_id = m.group(1), m.group(2)
        name = slug
        try:
            name = client.get(f"{greenhouse.API}/{slug}").json().get("name") or slug
        except Exception:
            pass
        jobs = greenhouse.fetch_jobs(client, slug, name)
    elif m := _ASHBY_URL.search(url):
        slug, job_id = m.group(1), m.group(2).lower()
        jobs = ashby.fetch_jobs(client, slug, slug)
    else:
        raise ValueError(f"not a Greenhouse or Ashby job link: {url}")
    for job in jobs:
        if job.job_id.lower() == job_id:
            return job
    raise ValueError(f"job {job_id} is not (or no longer) listed on the {slug} board")
