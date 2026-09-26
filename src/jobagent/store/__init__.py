from __future__ import annotations

from ..config import Config
from .base import Store


def open_store(cfg: Config) -> Store:
    if cfg.storage.backend == "supabase":
        from .supabase import SupabaseStore

        return SupabaseStore(cfg.storage.supabase_bucket)
    from .sqlite import SQLiteStore

    return SQLiteStore(cfg.path(cfg.storage.sqlite_path))
