"""Dual-level retrieval: local entity evidence combined with global community summaries.

Context assembly order matters and is deliberate: community summaries first (what this
region of the corpus is about), then entities and their relations (the specific facts),
then the source passages those facts came from. The passages are last because they are the
citation surface - everything above them has to be traceable down to one of them.
"""

from __future__ import annotations

from egraph.gateway.embeddings import Embedder
from egraph.retrieve.global_ import GlobalRetriever
from egraph.retrieve.local import LocalRetriever
from egraph.schemas import QueryRequest, RetrievalResult
from egraph.settings import Settings, get_settings
from egraph.store.base import GraphStore


class DualRetriever:
    def __init__(
        self, store: GraphStore, embedder: Embedder, settings: Settings | None = None
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.local = LocalRetriever(store, embedder)
        self.global_ = GlobalRetriever(store, embedder)

    def retrieve(self, request: QueryRequest) -> RetrievalResult:
        entities = []
        communities = []
        if request.mode in ("local", "dual"):
            entities = self.local.retrieve(
                request.question, request.top_k_entities, request.max_hops
            )
        if request.mode in ("global", "dual"):
            communities = self.global_.retrieve(request.question, request.top_k_communities)

        chunk_ids = self._rank_chunks(entities[: request.top_k_entities])

        return RetrievalResult(
            entities=entities,
            communities=communities,
            chunks=chunk_ids,
            context=self._build_context(entities, communities, chunk_ids, request),
            mode=request.mode,
        )

    def _rank_chunks(self, entities) -> list[str]:
        """Rank source passages by how much of the retrieved neighbourhood they support.

        A passage cited by several of the top entities is the one carrying the answer;
        citing every passage that any retrieved entity happens to appear in produces a
        citation list that names the whole corpus and means nothing.
        """
        weight: dict[str, float] = {}
        hits: dict[str, int] = {}
        for entity in entities:
            for chunk_id in entity.chunk_ids:
                weight[chunk_id] = weight.get(chunk_id, 0.0) + max(entity.score, 0.0)
                hits[chunk_id] = hits.get(chunk_id, 0) + 1
        return sorted(weight, key=lambda c: (-hits[c], -weight[c], c))

    def _build_context(self, entities, communities, chunk_ids, request: QueryRequest) -> str:
        budget = self.settings.max_context_chars
        blocks: list[str] = []

        if communities:
            lines = ["## Community summaries"]
            for community in communities:
                flag = " [STALE]" if community.dirty else ""
                lines.append(f"- {community.title}{flag}: {_flat(community.summary)}")
            blocks.append("\n".join(lines))

        if entities:
            lines = ["## Entities and relations"]
            for entity in entities[: request.top_k_entities]:
                # Relations ride on the entity's own line. Split across lines they read as
                # standalone three-word items and outrank the passages they came from.
                edges = "; ".join(entity.neighbours[:6])
                suffix = f" [relations: {edges}]" if edges else ""
                lines.append(
                    f"- {entity.canonical_name} ({entity.type}): "
                    f"{_flat(entity.description)}{suffix}"
                )
            blocks.append("\n".join(lines))

        if chunk_ids:
            lines = ["## Source passages"]
            for chunk_id in chunk_ids:
                chunk = self.store.get_chunk(chunk_id)
                if chunk is None:
                    continue
                lines.append(f"- [{chunk_id}] {_flat(chunk.text)}")
            blocks.append("\n".join(lines))

        context = "\n\n".join(blocks)
        if len(context) <= budget:
            return context
        # Truncate on a line boundary so a passage is never cut mid-fact.
        kept: list[str] = []
        used = 0
        for line in context.splitlines():
            if used + len(line) + 1 > budget:
                break
            kept.append(line)
            used += len(line) + 1
        return "\n".join(kept)


def _flat(text: str) -> str:
    """One context item must occupy exactly one line.

    Source text carries newlines; if they survive into the context, a downstream reader
    that works line-by-line sees a sentence fragment as a standalone item and can rank a
    three-word fragment above the passage it came from.
    """
    return " ".join(text.split())
