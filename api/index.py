"""Vercel entry point: serves the review dashboard (hosted mode)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent.web.hosted import build  # noqa: E402

app = build()
