"""Job queue abstraction.

``InlineQueue`` runs jobs synchronously in-process - the default, so tests, the CLI and a
laptop demo need no Redis. ``ArqQueue`` enqueues to Redis for the real deployment, where
the api container returns immediately and worker replicas do the expensive work.

The queue is also the backpressure point: ``depth()`` feeds the ``egraph_queue_depth``
gauge, and ``enqueue`` refuses new work past ``max_depth`` so a bulk ingest cannot bury the
workers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

from egraph.observability import metrics

log = logging.getLogger(__name__)


class QueueFullError(RuntimeError):
    pass


class JobQueue(Protocol):
    # True when enqueue() returns before the work is done. Callers that need to report what
    # a write actually changed check this instead of inferring it from the queue's type.
    synchronous: bool

    def enqueue(self, task: str, **kwargs: Any) -> str: ...
    def depth(self) -> int: ...


class InlineQueue:
    """Executes immediately. Keeps the same interface so call sites do not branch."""

    synchronous = True

    def __init__(self, pipeline_factory) -> None:
        self._pipeline_factory = pipeline_factory
        self.executed: list[tuple[str, dict]] = []

    def enqueue(self, task: str, **kwargs: Any) -> str:
        from egraph.workers import tasks

        job_id = f"inline-{uuid.uuid4().hex[:8]}"
        handler = tasks.TASKS.get(task)
        if handler is None:
            raise ValueError(f"unknown task {task}")
        self.executed.append((task, kwargs))
        try:
            handler(self._pipeline_factory(), **kwargs)
            metrics.JOBS.labels(task=task, result="ok").inc()
        except Exception:
            metrics.JOBS.labels(task=task, result="error").inc()
            raise
        return job_id

    def depth(self) -> int:
        return 0


class ArqQueue:
    """Redis-backed queue. Enqueue is sync (called from a request handler); arq's own
    client is async, so we push through a short-lived event loop."""

    synchronous = False

    def __init__(self, redis_url: str, queue_name: str, max_depth: int = 10_000) -> None:
        self.redis_url = redis_url
        self.queue_name = queue_name
        self.max_depth = max_depth
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        return self._pool

    async def enqueue_async(self, task: str, **kwargs: Any) -> str:
        pool = await self._get_pool()
        depth = await pool.zcard(self.queue_name)
        if depth >= self.max_depth:
            metrics.JOBS.labels(task=task, result="rejected").inc()
            raise QueueFullError(f"queue depth {depth} >= max_depth {self.max_depth}")
        job = await pool.enqueue_job(task, _queue_name=self.queue_name, **kwargs)
        metrics.QUEUE_DEPTH.set(depth + 1)
        return job.job_id if job else ""

    def enqueue(self, task: str, **kwargs: Any) -> str:
        return _run_sync(self.enqueue_async(task, **kwargs))

    def depth(self) -> int:
        try:
            return _run_sync(self._depth_async())
        except Exception as exc:  # noqa: BLE001 - depth is a gauge, not a correctness path
            log.debug("queue depth unavailable: %s", exc)
            return 0

    async def _depth_async(self) -> int:
        pool = await self._get_pool()
        depth = int(await pool.zcard(self.queue_name))
        metrics.QUEUE_DEPTH.set(depth)
        return depth


def _run_sync(coro):
    """Run a coroutine from sync code, whether or not a loop is already running."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from inside a running loop (a FastAPI handler): use a worker thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def build_queue(settings, pipeline_factory) -> JobQueue:
    if settings.queue_backend == "arq":
        return ArqQueue(settings.redis_url, settings.redis_queue_name)
    return InlineQueue(pipeline_factory)
