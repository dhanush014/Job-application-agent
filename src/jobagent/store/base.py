from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import Application, Job, Status


class Store(ABC):
    @abstractmethod
    def upsert_job(self, job: Job) -> bool:
        """Insert or refresh a job. Returns True if it was new."""

    @abstractmethod
    def list_jobs(self) -> list[Job]: ...

    @abstractmethod
    def save(self, app: Application) -> None: ...

    @abstractmethod
    def get(self, app_id: str) -> Application | None: ...

    @abstractmethod
    def list(self, statuses: list[Status] | None = None, limit: int = 2000) -> list[Application]: ...

    def upload(self, local: Path, key: str) -> None:
        """Mirror a file (resume, screenshot) to remote storage. No-op locally."""

    def count_applied_since(self, since: datetime) -> int:
        return sum(1 for a in self.list([Status.APPLIED]) if a.applied_at and a.applied_at >= since)

    def jobs_to_process(self, allow_reapply: bool, reapply_after_days: int) -> list[tuple[Job, int]]:
        """Jobs with no application yet, plus applied jobs due for a re-apply."""
        latest: dict[str, Application] = {}
        for a in self.list():
            cur = latest.get(a.job_key)
            if cur is None or a.created_at > cur.created_at:
                latest[a.job_key] = a
        cutoff = datetime.now(timezone.utc) - timedelta(days=reapply_after_days)
        out = []
        for job in self.list_jobs():
            a = latest.get(job.key)
            if a is None:
                out.append((job, 1))
            elif allow_reapply and a.status == Status.APPLIED and a.applied_at and a.applied_at < cutoff:
                out.append((job, a.attempt + 1))
        return out
