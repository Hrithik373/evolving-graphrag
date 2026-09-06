"""The systems under comparison.

All four share the same store, embedder and LLM backend, so the cost axis is measured in
the same units and the comparison is about *maintenance strategy*, nothing else.

* ``evolving``     - this system: provenance GC, chunk reuse, selective recompute.
* ``full-reindex`` - the naive production default: on any change, rebuild the index from
  the current corpus. Perfect freshness, worst cost. The upper bound to match.
* ``append-only``  - incremental adds, no retraction: updates append and deletes are
  ignored. Cheapest, and wrong after any deletion. The lower bound to beat on freshness.
* ``flat-vector``  - no graph at all: chunk embeddings and top-k passage retrieval.
  Deletion is easy for it (drop the chunks); multi-hop questions are not.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from egraph.gateway.client import LLMClient
from egraph.gateway.embeddings import Embedder
from egraph.ingest.chunker import chunk_document
from egraph.pipeline import Pipeline, build_pipeline
from egraph.schemas import Answer, DocumentChange, IndexStats, QueryRequest
from egraph.settings import Settings
from egraph.store.vectors import VectorIndex

log = logging.getLogger(__name__)


@dataclass
class UpdateCost:
    tokens: int = 0
    wall_ms: float = 0.0
    usd: float = 0.0
    communities_recomputed: int = 0
    changes: int = 0

    def as_dict(self) -> dict:
        return {
            "tokens": self.tokens,
            "wall_ms": round(self.wall_ms, 2),
            "usd": round(self.usd, 6),
            "communities_recomputed": self.communities_recomputed,
            "changes": self.changes,
            "tokens_per_change": round(self.tokens / self.changes, 1) if self.changes else 0.0,
        }


class System:
    """Common interface for everything the benchmark compares."""

    name = "system"

    def apply(self, change: DocumentChange) -> None:
        raise NotImplementedError

    def settle(self) -> None:
        """Finish any deferred work. Called before questions are asked, so every system is
        judged at its best rather than mid-flight."""

    def query(self, question: str) -> Answer:
        raise NotImplementedError

    def stats(self) -> IndexStats:
        raise NotImplementedError

    def cost(self) -> UpdateCost:
        raise NotImplementedError

    def reset_cost(self) -> None:
        raise NotImplementedError


class PipelineSystem(System):
    """Base for the three graph systems - they differ only in maintenance policy."""

    name = "pipeline"

    def __init__(self, settings: Settings, pipeline: Pipeline | None = None) -> None:
        self.settings = settings
        self.pipeline = pipeline or build_pipeline(settings)
        self.pipeline.reset()
        self._cost = UpdateCost()
        self._baseline = self.pipeline.meter.snapshot()

    def query(self, question: str) -> Answer:
        return self.pipeline.query(
            QueryRequest(
                question=question,
                top_k_entities=self.settings.top_k_entities,
                top_k_communities=self.settings.top_k_communities,
            )
        )

    def stats(self) -> IndexStats:
        return self.pipeline.stats()

    def cost(self) -> UpdateCost:
        return self._cost

    def reset_cost(self) -> None:
        self._cost = UpdateCost()
        self._baseline = self.pipeline.meter.snapshot()

    def _charge(self, started: float, recomputed: int = 0) -> None:
        now = self.pipeline.meter.snapshot()
        self._cost.tokens += int(now["tokens"] - self._baseline["tokens"])
        self._cost.usd += float(now["usd"]) - float(self._baseline["usd"])
        self._cost.wall_ms += (time.perf_counter() - started) * 1000
        self._cost.communities_recomputed += recomputed
        self._cost.changes += 1
        self._baseline = now


class EvolvingSystem(PipelineSystem):
    """This project. Incremental churn plus selective, bounded recompute."""

    name = "evolving"

    def __init__(self, settings: Settings, recompute_budget: int | None = None) -> None:
        super().__init__(settings)
        # None = drain fully (freshest). An integer caps how many communities may be
        # re-summarised per change: the knob the Pareto sweep turns.
        self.recompute_budget = recompute_budget

    def apply(self, change: DocumentChange) -> None:
        started = time.perf_counter()
        self.pipeline.apply(change)
        if self.recompute_budget is None:
            report = self.pipeline.drain()
        else:
            report = self.pipeline.recompute_dirty(batch_size=self.recompute_budget)
        self._charge(started, report.recomputed)

    def settle(self) -> None:
        # Under a budget the index is deliberately allowed to lag; settling would erase
        # exactly the freshness/cost trade-off being measured. So: only the unbudgeted
        # configuration settles.
        if self.recompute_budget is None:
            self.pipeline.drain()


class FullReindexSystem(PipelineSystem):
    """Rebuild everything on every change. Correct by brute force.

    Two modelling decisions, both stated rather than buried:

    * The cost meter is *not* reset by the rebuild - only the index is. Resetting it would
      make a rebuild appear free.
    * The extraction cache is dropped on every rebuild. A content-hash extraction cache is
      part of this project's incremental design, not of the naive default; leaving it in
      place would credit the baseline with the optimisation being evaluated and understate
      the true cost of reindexing.
    """

    name = "full-reindex"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.corpus: dict[str, str] = {}

    def apply(self, change: DocumentChange) -> None:
        started = time.perf_counter()
        if change.op == "delete":
            self.corpus.pop(change.doc_id, None)
        else:
            self.corpus[change.doc_id] = change.content or ""

        # The naive production default: throw the index away and build it again.
        self.pipeline.clear_index()
        if hasattr(self.pipeline.cache, "clear"):
            self.pipeline.cache.clear()
        for doc_id, content in self.corpus.items():
            self.pipeline.apply(
                DocumentChange(op="add", doc_id=doc_id, uri=doc_id, content=content)
            )
        report = self.pipeline.drain()
        self._charge(started, report.recomputed)


class AppendOnlySystem(PipelineSystem):
    """Incremental adds with no retraction path - the naive incremental index.

    Updates append their new chunks without withdrawing the old ones, and deletes do
    nothing at all. Cheap, and it keeps answering with facts that were removed from the
    corpus, which is precisely what the deletion scenario measures.
    """

    name = "append-only"

    def apply(self, change: DocumentChange) -> None:
        started = time.perf_counter()
        recomputed = 0
        if change.op == "delete":
            # Marks the document deleted but retracts nothing from the graph.
            doc = self.pipeline.store.get_document(change.doc_id)
            if doc is not None:
                doc.status = "deleted"
                self.pipeline.store.upsert_document(doc)
        else:
            self.pipeline.apply(
                DocumentChange(
                    op="add",
                    doc_id=f"{change.doc_id}#v{self._version(change.doc_id)}",
                    uri=change.uri or change.doc_id,
                    content=change.content,
                )
            )
            recomputed = self.pipeline.drain().recomputed
        self._charge(started, recomputed)

    def _version(self, doc_id: str) -> int:
        return sum(1 for d in self.pipeline.store.list_documents() if d.doc_id.startswith(doc_id))


@dataclass
class _FlatChunk:
    chunk_id: str
    doc_id: str
    text: str


class FlatVectorSystem(System):
    """Vanilla vector RAG: chunks, embeddings, top-k passages. No graph, no summaries."""

    name = "flat-vector"

    def __init__(self, settings: Settings, meter, llm: LLMClient, embedder: Embedder) -> None:
        self.settings = settings
        self.meter = meter
        self.llm = llm
        self.embedder = embedder
        self.index = VectorIndex(settings.embed_dim)
        self.chunks: dict[str, _FlatChunk] = {}
        self.by_doc: dict[str, list[str]] = {}
        self._cost = UpdateCost()
        self._baseline = meter.snapshot()

    def apply(self, change: DocumentChange) -> None:
        started = time.perf_counter()
        for chunk_id in self.by_doc.pop(change.doc_id, []):
            self.chunks.pop(chunk_id, None)
            self.index.remove(chunk_id)
        if change.op != "delete" and change.content:
            pieces = chunk_document(
                change.doc_id, change.content, self.settings.chunk_size, self.settings.chunk_overlap
            )
            embeddings = self.embedder.embed_batch([p.text for p in pieces])
            ids = []
            for piece, embedding in zip(pieces, embeddings, strict=True):
                self.chunks[piece.chunk_id] = _FlatChunk(piece.chunk_id, change.doc_id, piece.text)
                self.index.upsert(piece.chunk_id, embedding)
                ids.append(piece.chunk_id)
            self.by_doc[change.doc_id] = ids
        self._charge(started)

    def query(self, question: str) -> Answer:
        started = time.perf_counter()
        hits = self.index.search(self.embedder.embed(question), self.settings.top_k_entities)
        lines, citations = [], []
        for chunk_id, _ in hits:
            chunk = self.chunks.get(chunk_id)
            if chunk is None:
                continue
            lines.append(f"- [{chunk.chunk_id}] {' '.join(chunk.text.split())}")
            if chunk.doc_id not in citations:
                citations.append(chunk.doc_id)
        context = "## Source passages\n" + "\n".join(lines) if lines else ""
        text = (
            self.llm.answer(question, context)
            if context
            else "The indexed sources do not contain this information."
        )
        return Answer(
            text=text,
            citations=citations,
            mode="local",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def stats(self) -> IndexStats:
        return IndexStats(
            documents=len(self.by_doc),
            active_documents=len(self.by_doc),
            chunks=len(self.chunks),
        )

    def cost(self) -> UpdateCost:
        return self._cost

    def reset_cost(self) -> None:
        self._cost = UpdateCost()
        self._baseline = self.meter.snapshot()

    def _charge(self, started: float) -> None:
        now = self.meter.snapshot()
        self._cost.tokens += int(now["tokens"] - self._baseline["tokens"])
        self._cost.usd += float(now["usd"]) - float(self._baseline["usd"])
        self._cost.wall_ms += (time.perf_counter() - started) * 1000
        self._cost.changes += 1
        self._baseline = now


@dataclass
class SystemRegistry:
    settings: Settings
    systems: dict[str, System] = field(default_factory=dict)

    def build(self, names: list[str], recompute_budget: int | None = None) -> dict[str, System]:
        from egraph.cost.meter import CostMeter
        from egraph.store.memory import MemoryStore

        built: dict[str, System] = {}
        for name in names:
            if name == "evolving":
                built[name] = EvolvingSystem(self.settings, recompute_budget)
            elif name == "full-reindex":
                built[name] = FullReindexSystem(self.settings)
            elif name == "append-only":
                built[name] = AppendOnlySystem(self.settings)
            elif name == "flat-vector":
                store = MemoryStore(dim=self.settings.embed_dim)
                meter = CostMeter(store, self.settings)
                built[name] = FlatVectorSystem(
                    self.settings,
                    meter,
                    LLMClient(meter, self.settings),
                    Embedder(meter, self.settings),
                )
            else:
                raise ValueError(f"unknown system {name}")
        self.systems = built
        return built


ALL_SYSTEMS = ["evolving", "full-reindex", "append-only", "flat-vector"]
