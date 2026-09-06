"""Local retrieval: entity vector search plus a graph hop.

Vector search alone returns entities that *look like* the question. The hop is what makes
this GraphRAG: it pulls in the neighbours those entities are actually connected to, and
those edges carry provenance, which is what the answer cites.
"""

from __future__ import annotations

from egraph.gateway.embeddings import Embedder
from egraph.schemas import RetrievedEntity
from egraph.store.base import GraphStore


class LocalRetriever:
    def __init__(self, store: GraphStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    def retrieve(self, question: str, top_k: int = 8, max_hops: int = 1) -> list[RetrievedEntity]:
        embedding = self.embedder.embed(question)
        seeds = self.store.search_entities(embedding, top_k)
        scores: dict[str, float] = {}
        order: list[str] = []
        for entity, score in seeds:
            scores[entity.entity_id] = score
            order.append(entity.entity_id)

        frontier = list(order)
        for hop in range(max_hops):
            decay = 0.5 ** (hop + 1)
            next_frontier: list[str] = []
            for entity_id in frontier:
                for relation in self.store.relations_for_entity(entity_id):
                    other = (
                        relation.target_id
                        if relation.source_id == entity_id
                        else relation.source_id
                    )
                    # Support-weighted: an edge asserted by several chunks pulls harder.
                    contribution = (
                        scores[entity_id] * decay * min(1.0, len(relation.provenance) / 2)
                    )
                    if other not in scores:
                        scores[other] = contribution
                        order.append(other)
                        next_frontier.append(other)
                    else:
                        scores[other] = max(scores[other], contribution)
            frontier = next_frontier
            if not frontier:
                break

        results: list[RetrievedEntity] = []
        for entity_id in order:
            entity = self.store.get_entity(entity_id)
            if entity is None:
                continue
            neighbours = []
            for relation in self.store.relations_for_entity(entity_id):
                other_id = (
                    relation.target_id if relation.source_id == entity_id else relation.source_id
                )
                other = self.store.get_entity(other_id)
                if other is not None:
                    neighbours.append(f"{relation.relation_type} -> {other.canonical_name}")
            results.append(
                RetrievedEntity(
                    entity_id=entity.entity_id,
                    canonical_name=entity.canonical_name,
                    type=entity.type,
                    description=entity.description,
                    score=round(scores[entity_id], 5),
                    community_id=entity.community_id,
                    chunk_ids=sorted(entity.mentions),
                    neighbours=neighbours[:8],
                )
            )
        results.sort(key=lambda r: -r.score)
        return results[: top_k * 2]
