"""Extraction cache, keyed by chunk content hash.

Two things make this more than an optimisation:

* Under churn, an edited document re-chunks to *mostly the same chunks*. Every unchanged
  chunk hits this cache, so the cost of an update scales with the size of the edit rather
  than the size of the document. That is a headline result, and ``extraction_cache_hits``
  is reported in the eval.
* It makes re-ingest idempotent in cost as well as in effect.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


def cache_key(text: str, model: str, prompt_version: str = "v1") -> str:
    """Content-addressed. Changing the prompt or model changes the key, so a stale
    extraction can never survive a prompt edit."""
    payload = f"{prompt_version}|{model}|{text}"
    return "egraph:extract:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


class ExtractionCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any]) -> None: ...
    @property
    def hits(self) -> int: ...
    @property
    def misses(self) -> int: ...


class MemoryCache:
    """Process-local cache. Used by tests, the eval harness and single-process demos."""

    def __init__(self, max_entries: int = 20_000) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self.max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._data.get(key)
        if value is None:
            self._misses += 1
            return None
        self._hits += 1
        return json.loads(json.dumps(value))  # defensive copy

    def set(self, key: str, value: dict[str, Any]) -> None:
        if len(self._data) >= self.max_entries:
            self._data.pop(next(iter(self._data)))
        self._data[key] = value

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def clear(self) -> None:
        self._data.clear()
        self._hits = 0
        self._misses = 0


class RedisCache:
    """Shared cache across api + worker replicas. Falls back to a miss on any Redis error -
    a cache outage must degrade cost, never correctness."""

    def __init__(self, url: str, ttl_s: int = 60 * 60 * 24 * 30) -> None:
        import redis

        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self.ttl_s = ttl_s
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self._redis.get(key)
        except Exception as exc:  # noqa: BLE001
            log.warning("extraction cache read failed: %s", exc)
            return None
        if raw is None:
            self._misses += 1
            return None
        self._hits += 1
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        try:
            self._redis.setex(key, self.ttl_s, json.dumps(value))
        except Exception as exc:  # noqa: BLE001
            log.warning("extraction cache write failed: %s", exc)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses


def build_cache(backend: str, redis_url: str) -> ExtractionCache:
    if backend == "arq":  # workers share Redis; use it for the cache too
        try:
            return RedisCache(redis_url)
        except Exception as exc:  # noqa: BLE001 - Redis optional in dev
            log.warning("falling back to in-process extraction cache: %s", exc)
    return MemoryCache()
