"""Worker tasks: everything expensive, off the request path.

Each task is a plain function of ``(pipeline, ...)`` so it can be called directly by the
inline queue, by the CLI and by tests; the ``arq_*`` wrappers adapt them to arq's
``(ctx, ...)`` signature. Keeping the logic out of the wrappers is what lets the whole
async design be tested without Redis.
"""

from __future__ import annotations

import logging
from typing import Any

from egraph.observability import metrics
from egraph.pipeline import Pipeline

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------- tasks
def ingest_chunks(pipeline: Pipeline, doc_id: str, chunk_ids: list[str]) -> dict[str, Any]:
    """Embed + extract + graft new chunks, then mark the affected communities dirty."""
    count = pipeline.ingest_chunks(doc_id, chunk_ids)
    # Grafting new entities always dirties something; queue the recompute behind it.
    if pipeline.queue is not None and not _is_inline(pipeline.queue):
        pipeline.queue.enqueue("recompute_dirty")
    return {"doc_id": doc_id, "chunks": len(chunk_ids), "mutations": count}


def recompute_dirty(pipeline: Pipeline, batch_size: int | None = None) -> dict[str, Any]:
    """Re-summarise one bounded batch of dirty communities. Never touches clean ones."""
    report = pipeline.recompute_dirty(batch_size)
    # Still dirty after a bounded batch: come back for the rest rather than looping here,
    # so one huge edit cannot monopolise a worker.
    if report.remaining_dirty and pipeline.queue is not None and not _is_inline(pipeline.queue):
        pipeline.queue.enqueue("recompute_dirty")
    return report.as_dict()


def compact(pipeline: Pipeline) -> dict[str, Any]:
    """Full re-clustering pass. Scheduled, amortised, off the hot path."""
    report = pipeline.compact()
    return report.as_dict()


def refresh_metrics(pipeline: Pipeline) -> dict[str, Any]:
    """Cron tick that keeps the gauges live even when nothing is being written."""
    staleness = pipeline.staleness()
    metrics.observe_index(pipeline.store.stats())
    return staleness.model_dump(mode="json")


TASKS = {
    "ingest_chunks": ingest_chunks,
    "recompute_dirty": recompute_dirty,
    "compact": compact,
    "refresh_metrics": refresh_metrics,
}


def _is_inline(queue: object) -> bool:
    """The inline queue already ran the follow-up work; re-enqueueing would recurse.
    Checked by name so this module never imports the queue module that imports it."""
    return type(queue).__name__ == "InlineQueue"


# ---------------------------------------------------------------------- arq adapters
async def arq_ingest_chunks(ctx: dict, doc_id: str, chunk_ids: list[str]) -> dict[str, Any]:
    return _run(ctx, ingest_chunks, doc_id=doc_id, chunk_ids=chunk_ids)


async def arq_recompute_dirty(ctx: dict, batch_size: int | None = None) -> dict[str, Any]:
    return _run(ctx, recompute_dirty, batch_size=batch_size)


async def arq_compact(ctx: dict) -> dict[str, Any]:
    return _run(ctx, compact)


async def arq_refresh_metrics(ctx: dict) -> dict[str, Any]:
    return _run(ctx, refresh_metrics)


def _run(ctx: dict, task, **kwargs) -> dict[str, Any]:
    pipeline: Pipeline = ctx["pipeline"]
    name = task.__name__
    try:
        result = task(pipeline, **kwargs)
        metrics.JOBS.labels(task=name, result="ok").inc()
        return result
    except Exception:
        metrics.JOBS.labels(task=name, result="error").inc()
        log.exception("task %s failed", name)
        raise
