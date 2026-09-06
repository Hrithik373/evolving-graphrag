"""arq worker entrypoint.

Run with ``arq egraph.workers.worker.WorkerSettings``. Scaling the number of these
replicas is the throughput knob for the whole system - the api container stays at one.

The worker also serves its own ``/metrics`` on ``WORKER_METRICS_PORT`` so Prometheus can
scrape the cost and recompute counters, which are produced here rather than in the api.
"""

from __future__ import annotations

import logging
import os

from arq import cron, func
from arq.connections import RedisSettings

from egraph.pipeline import build_pipeline
from egraph.settings import get_settings
from egraph.workers import tasks

log = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    from egraph.workers.queue import ArqQueue

    queue = ArqQueue(settings.redis_url, settings.redis_queue_name)
    ctx["pipeline"] = build_pipeline(settings, queue=queue)
    ctx["settings"] = settings

    if settings.metrics_enabled:
        from prometheus_client import start_http_server

        port = int(os.getenv("WORKER_METRICS_PORT", "9100"))
        try:
            start_http_server(port)
            log.info("worker metrics on :%d", port)
        except OSError as exc:  # a second replica on the same host
            log.warning("worker metrics port %d unavailable: %s", port, exc)

    log.info("worker ready (store=%s llm=%s)", settings.store_backend, settings.llm_backend)


async def shutdown(ctx: dict) -> None:
    pipeline = ctx.get("pipeline")
    if pipeline is not None:
        pipeline.close()


def _cron_jobs():
    settings = get_settings()
    jobs = [
        # Keeps the freshness gauges live even during an idle period.
        cron(tasks.arq_refresh_metrics, second={0, 15, 30, 45}, run_at_startup=True),
    ]
    if settings.compaction_enabled:
        minutes = max(1, settings.compaction_interval_s // 60)
        jobs.append(cron(tasks.arq_compact, minute=set(range(0, 60, minutes)) or {0}))
    return jobs


class WorkerSettings:
    functions = [
        func(tasks.arq_ingest_chunks, name="ingest_chunks", max_tries=3),
        func(tasks.arq_recompute_dirty, name="recompute_dirty", max_tries=3),
        func(tasks.arq_compact, name="compact", max_tries=1),
        func(tasks.arq_refresh_metrics, name="refresh_metrics", max_tries=1),
    ]
    cron_jobs = _cron_jobs()
    on_startup = startup
    on_shutdown = shutdown
    queue_name = get_settings().redis_queue_name
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = int(os.getenv("WORKER_MAX_JOBS", "10"))
    job_timeout = int(os.getenv("WORKER_JOB_TIMEOUT", "600"))
    keep_result = 3600
