"""Answer generation with provenance citations and an explicit freshness signal."""

from __future__ import annotations

import time

from egraph.gateway.client import LLMClient
from egraph.observability import metrics
from egraph.retrieve.dual import DualRetriever
from egraph.schemas import Answer, QueryRequest
from egraph.store.base import GraphStore


class AnswerEngine:
    def __init__(self, store: GraphStore, retriever: DualRetriever, llm: LLMClient) -> None:
        self.store = store
        self.retriever = retriever
        self.llm = llm

    def answer(self, request: QueryRequest) -> Answer:
        started = time.perf_counter()
        result = self.retriever.retrieve(request)
        text = (
            self.llm.answer(request.question, result.context)
            if result.context
            else ("The indexed sources do not contain this information.")
        )

        # Citations are document ids where we can resolve them, chunk ids otherwise -
        # both trace back to a real source, which is the point.
        citations: list[str] = []
        for chunk_id in result.chunks:
            chunk = self.store.get_chunk(chunk_id)
            reference = chunk.doc_id if chunk is not None else chunk_id
            if reference not in citations:
                citations.append(reference)

        stale = sum(1 for community in result.communities if community.dirty)
        latency = (time.perf_counter() - started) * 1000
        metrics.QUERY_LATENCY.labels(mode=request.mode).observe(latency)
        metrics.STALE_TOUCHED.observe(stale)

        return Answer(
            text=text,
            citations=citations[:10],
            used_communities=[c.community_id for c in result.communities],
            used_entities=[e.entity_id for e in result.entities[: request.top_k_entities]],
            stale_communities_touched=stale,
            mode=request.mode,
            latency_ms=round(latency, 2),
            tokens=len(result.context) // 4,
        )
