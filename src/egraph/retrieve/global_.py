"""Global retrieval: community summaries as the corpus-level view.

Every community returned reports whether its summary is currently dirty. That flag is
carried all the way into the :class:`Answer` as ``stale_communities_touched``, so a query
answered from a summary that has not caught up with a recent edit says so rather than
looking confidently fresh.
"""

from __future__ import annotations

from egraph.gateway.embeddings import Embedder
from egraph.schemas import RetrievedCommunity
from egraph.store.base import GraphStore
from egraph.store.vectors import cosine


class GlobalRetriever:
    def __init__(self, store: GraphStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    def retrieve(self, question: str, top_k: int = 3) -> list[RetrievedCommunity]:
        embedding = self.embedder.embed(question)
        scored: list[tuple[float, object]] = []
        for community in self.store.all_communities():
            if not community.summary:
                continue
            if community.embedding:
                score = cosine(embedding, community.embedding)
            else:
                score = cosine(embedding, self.embedder.embed(community.summary))
            scored.append((score, community))
        scored.sort(key=lambda pair: -pair[0])
        return [
            RetrievedCommunity(
                community_id=community.community_id,
                title=community.title,
                summary=community.summary,
                score=round(float(score), 5),
                dirty=community.dirty,
                member_count=community.member_count,
            )
            for score, community in scored[:top_k]
        ]
