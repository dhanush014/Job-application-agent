"""Build the dashboard for serverless hosting (Vercel) from environment variables.

Required env: DASHBOARD_PASSWORD, SUPABASE_URL, SUPABASE_KEY, JOBAGENT_CONFIG
(contents of config.yaml with storage.backend: supabase) and
JOBAGENT_MASTER_RESUME (contents of profile/master_resume.yaml).
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

REQUIRED = ["DASHBOARD_PASSWORD", "SUPABASE_URL", "SUPABASE_KEY", "JOBAGENT_CONFIG", "JOBAGENT_MASTER_RESUME"]


def build() -> FastAPI:
    os.environ.setdefault("JOBAGENT_DATA_DIR", "/tmp/jobagent")  # the only writable place
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        return _error("Missing environment variables in the Vercel project: " + ", ".join(missing))
    try:
        from ..config import load_config
        from ..pipeline import Pipeline
        from ..store import open_store
        from .app import create_app

        cfg = load_config("/nonexistent/config.yaml")  # everything comes from env
        if cfg.storage.backend != "supabase":
            return _error("JOBAGENT_CONFIG must set storage.backend: supabase (Vercel has no disk)")
        return create_app(Pipeline(cfg, open_store(cfg), llm=None), hosted=True)
    except Exception as e:  # show setup problems instead of a blank 500
        return _error(f"Startup failed: {type(e).__name__}: {e}")


def _error(message: str) -> FastAPI:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    def err(path: str):
        return PlainTextResponse(message, 500)

    return app
