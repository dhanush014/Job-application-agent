from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import Application, Job, Status
from .base import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  key TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY,
  job_key TEXT NOT NULL,
  status TEXT NOT NULL,
  company TEXT, title TEXT, score INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, applied_at TEXT,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS applications_job ON applications(job_key);
"""


class SQLiteStore(Store):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def upsert_job(self, job: Job) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            exists = c.execute("SELECT 1 FROM jobs WHERE key=?", (job.key,)).fetchone()
            if exists:
                c.execute("UPDATE jobs SET data=?, last_seen=? WHERE key=?", (job.model_dump_json(), now, job.key))
            else:
                c.execute("INSERT INTO jobs VALUES (?,?,?,?)", (job.key, job.model_dump_json(), now, now))
        return not exists

    def list_jobs(self) -> list[Job]:
        with self._conn() as c:
            return [Job.model_validate_json(r[0]) for r in c.execute("SELECT data FROM jobs")]

    def save(self, app: Application) -> None:
        app.updated_at = datetime.now(timezone.utc)
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO applications VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    app.id, app.job_key, app.status.value, app.company_name, app.title, app.score,
                    app.created_at.isoformat(), app.updated_at.isoformat(),
                    app.applied_at.isoformat() if app.applied_at else None,
                    app.model_dump_json(),
                ),
            )

    def get(self, app_id: str) -> Application | None:
        with self._conn() as c:
            r = c.execute("SELECT data FROM applications WHERE id=?", (app_id,)).fetchone()
        return Application.model_validate_json(r[0]) if r else None

    def list(self, statuses: list[Status] | None = None, limit: int = 2000) -> list[Application]:
        q, args = "SELECT data FROM applications", []
        if statuses:
            q += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args = [s.value for s in statuses]
        q += " ORDER BY updated_at DESC LIMIT ?"
        with self._conn() as c:
            return [Application.model_validate_json(r[0]) for r in c.execute(q, (*args, limit))]
