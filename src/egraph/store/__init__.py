"""Storage: one multi-model store holds the graph, the vectors and the audit trails."""

from __future__ import annotations

import logging
from functools import lru_cache

from egraph.settings import Settings, get_settings
from egraph.store.base import GraphStore
from egraph.store.memory import MemoryStore

log = logging.getLogger(__name__)


def build_store(settings: Settings | None = None) -> GraphStore:
    cfg = settings or get_settings()
    if cfg.store_backend == "arcadedb":
        from egraph.store.arcadedb import ArcadeDBStore
        from egraph.store.client import ArcadeDBClient

        client = ArcadeDBClient(
            url=cfg.arcadedb_url,
            database=cfg.arcadedb_database,
            user=cfg.arcadedb_user,
            password=cfg.arcadedb_password,
        )
        store: GraphStore = ArcadeDBStore(client, dim=cfg.embed_dim)
    else:
        store = MemoryStore(dim=cfg.embed_dim, persist_path=cfg.memory_store_path or None)
    store.migrate()
    log.info("store backend=%s ready", cfg.store_backend)
    return store


@lru_cache(maxsize=1)
def get_store() -> GraphStore:
    """Process-wide store singleton (FastAPI dependency, worker context, CLI)."""
    return build_store()


def reset_store_cache() -> None:
    get_store.cache_clear()


__all__ = ["GraphStore", "MemoryStore", "build_store", "get_store", "reset_store_cache"]
