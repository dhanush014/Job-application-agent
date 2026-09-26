"""Supabase (free tier) backend. Run supabase/schema.sql once in the SQL editor.

Env: SUPABASE_URL, SUPABASE_KEY (service role key; keep it out of git).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..models import Application, Job, Status
from .base import Store


class SupabaseStore(Store):
    def __init__(self, bucket: str, client=None):
        if client is None:
            from supabase import create_client

            client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        self.db = client
        self.bucket = bucket

    def upsert_job(self, job: Job) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.db.table("jobs").select("key").eq("key", job.key).execute().data
        row = {"key": job.key, **job.model_dump(mode="json"), "last_seen": now}
        if not existing:
            row["first_seen"] = now
        self.db.table("jobs").upsert(row).execute()
        return not existing

    def list_jobs(self) -> list[Job]:
        rows = self._all(self.db.table("jobs").select("*"))
        return [Job.model_validate({k: v for k, v in r.items() if k in Job.model_fields}) for r in rows]

    def save(self, app: Application) -> None:
        app.updated_at = datetime.now(timezone.utc)
        self.db.table("applications").upsert(app.model_dump(mode="json")).execute()

    def get(self, app_id: str) -> Application | None:
        rows = self.db.table("applications").select("*").eq("id", app_id).execute().data
        return Application.model_validate(rows[0]) if rows else None

    def list(self, statuses: list[Status] | None = None, limit: int = 2000) -> list[Application]:
        q = self.db.table("applications").select("*").order("updated_at", desc=True)
        if statuses:
            q = q.in_("status", [s.value for s in statuses])
        return [Application.model_validate(r) for r in self._all(q, limit)]

    def upload(self, local: Path, key: str) -> None:
        ctype = "application/pdf" if local.suffix == ".pdf" else "image/png"
        self.db.storage.from_(self.bucket).upload(
            key, local.read_bytes(), {"content-type": ctype, "upsert": "true"}
        )

    def download(self, key: str) -> bytes | None:
        try:
            return self.db.storage.from_(self.bucket).download(key)
        except Exception:
            return None

    @staticmethod
    def _all(query, limit: int = 100_000) -> list[dict]:
        out, page = [], 1000
        while len(out) < limit:
            rows = query.range(len(out), len(out) + page - 1).execute().data
            out += rows
            if len(rows) < page:
                break
        return out[:limit]
