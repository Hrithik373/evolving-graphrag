"""Composition root.

The API process, the worker process, the CLI and the eval harness all need the same object
graph wired the same way. Building it in one place keeps them honest with each other - a
result produced by ``make eval`` comes from exactly the components the running service uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from egraph.churn.compact import CompactionReport, compact
from egraph.churn.dirty import DirtyTracker
from egraph.churn.engine import ChurnEngine
from egraph.cost.meter import CostMeter
from egraph.extract.cache import ExtractionCache, build_cache
from egraph.extract.entities import Extractor
from egraph.gateway.client import LLMClient
from egraph.gateway.embeddings import Embedder
from egraph.ingest.resolver import EntityResolver
from egraph.observability import metrics
from egraph.retrieve.answer import AnswerEngine
from egraph.retrieve.dual import DualRetriever
from egraph.schemas import Answer, ChurnResult, DocumentChange, IndexStats, QueryRequest, Staleness
from egraph.settings import Settings, get_settings
from egraph.store import build_store
from egraph.store.base import GraphStore
from egraph.summarize.recompute import RecomputeReport, SummaryRecomputer
from egraph.summarize.summarizer import Summarizer

log = logging.getLogger(__name__)


@dataclass
class Pipeline:
    """Everything wired together. One per process."""

    settings: Settings
    store: GraphStore
    meter: CostMeter
    llm: LLMClient
    embedder: Embedder
    cache: ExtractionCache
    extractor: Extractor
    resolver: EntityResolver
    engine: ChurnEngine
    retriever: DualRetriever
    answerer: AnswerEngine
    summarizer: Summarizer
    recomputer: SummaryRecomputer
    dirty: DirtyTracker
    queue: object | None = None

    # ------------------------------------------------------------------ write path
    def apply(self, change: DocumentChange) -> ChurnResult:
        result = self.engine.apply(change)
        metrics.observe_index(self.store.stats())
        return result

    def ingest_chunks(self, doc_id: str, chunk_ids: list[str]) -> int:
        return len(self.engine.ingest_chunks(doc_id, chunk_ids))

    def recompute_dirty(self, batch_size: int | None = None) -> RecomputeReport:
        return self.recomputer.recompute_dirty(batch_size)

    def drain(self) -> RecomputeReport:
        return self.recomputer.drain()

    def compact(self) -> CompactionReport:
        report = compact(self.store, self.settings)
        metrics.observe_index(self.store.stats())
        return report

    # ------------------------------------------------------------------ read path
    def query(self, request: QueryRequest) -> Answer:
        return self.answerer.answer(request)

    def staleness(self) -> Staleness:
        depth = 0
        if self.queue is not None and hasattr(self.queue, "depth"):
            depth = self.queue.depth()
        return self.dirty.staleness(queue_depth=depth)

    def stats(self) -> IndexStats:
        stats = self.store.stats()
        metrics.observe_index(stats)
        return stats

    # ------------------------------------------------------------------ lifecycle
    def clear_index(self) -> None:
        """Drop the graph but keep the cost meter running.

        This is what a full reindex does, and the distinction matters: if clearing the
        index also cleared the meter, a rebuild would appear to cost nothing.
        """
        self.store.clear()

    def reset(self) -> None:
        """Wipe the index *and* the accounting. Used between eval runs, never in production."""
        self.clear_index()
        self.meter.reset()
        if hasattr(self.cache, "clear"):
            self.cache.clear()

    def close(self) -> None:
        self.llm.close()
        self.store.close()


def build_pipeline(
    settings: Settings | None = None,
    store: GraphStore | None = None,
    queue=None,
) -> Pipeline:
    cfg = settings or get_settings()
    graph_store = store or build_store(cfg)
    meter = CostMeter(graph_store, cfg)
    llm = LLMClient(meter, cfg)
    embedder = Embedder(meter, cfg)
    cache = build_cache(cfg.queue_backend, cfg.redis_url)
    extractor = Extractor(llm, cache)
    resolver = EntityResolver(graph_store, cfg)
    engine = ChurnEngine(graph_store, embedder, extractor, resolver, queue=queue, settings=cfg)
    retriever = DualRetriever(graph_store, embedder, cfg)
    answerer = AnswerEngine(graph_store, retriever, llm)
    summarizer = Summarizer(graph_store, llm, embedder, cfg.summary_max_entities)
    recomputer = SummaryRecomputer(graph_store, summarizer, cfg)

    log.info(
        "pipeline ready: store=%s queue=%s llm=%s embed=%s",
        cfg.store_backend,
        cfg.queue_backend,
        cfg.llm_backend,
        cfg.embed_backend,
    )
    return Pipeline(
        settings=cfg,
        store=graph_store,
        meter=meter,
        llm=llm,
        embedder=embedder,
        cache=cache,
        extractor=extractor,
        resolver=resolver,
        engine=engine,
        retriever=retriever,
        answerer=answerer,
        summarizer=summarizer,
        recomputer=recomputer,
        dirty=engine.dirty,
        queue=queue,
    )
