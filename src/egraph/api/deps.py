"""FastAPI dependencies: one pipeline and one queue per process."""

from __future__ import annotations

import logging
from functools import lru_cache

from egraph.pipeline import Pipeline, build_pipeline
from egraph.settings import Settings, get_settings
from egraph.workers.queue import JobQueue, build_queue

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    settings = get_settings()
    # The queue needs a pipeline (to run inline jobs) and the pipeline needs a queue (to
    # enqueue them). Break the cycle with a late-bound factory rather than two instances -
    # two pipelines would mean two stores and two cost meters.
    holder: dict[str, Pipeline] = {}
    queue: JobQueue = build_queue(settings, lambda: holder["pipeline"])
    pipeline = build_pipeline(settings, queue=queue)
    holder["pipeline"] = pipeline
    return pipeline


def get_settings_dep() -> Settings:
    return get_settings()


def reset_pipeline_cache() -> None:
    get_pipeline.cache_clear()
