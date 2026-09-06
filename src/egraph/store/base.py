"""The storage contract.

Deliberately primitive: every higher-level, provenance-aware operation is composed from
these in ``store/entities.py``, ``store/relations.py`` and ``store/communities.py`` so that
both backends get identical churn semantics for free.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from egraph.schemas import (
    Chunk,
    Community,
    CostRecord,
    Entity,
    IndexStats,
    Relation,
    ResolutionDecision,
    SourceDocument,
)


class GraphStore(ABC):
    """Graph + vector storage. One store holds documents, chunks, entities, relations,
    communities, resolution decisions and cost records."""

    # ------------------------------------------------------------------ lifecycle
    @abstractmethod
    def migrate(self) -> None:
        """Create types/indexes if missing. Idempotent."""

    @abstractmethod
    def ping(self) -> bool:
        """Readiness probe."""

    @abstractmethod
    def clear(self) -> None:
        """Drop all data. Used by eval runs and tests."""

    def close(self) -> None:  # pragma: no cover - optional
        return None

    # ------------------------------------------------------------------ documents
    @abstractmethod
    def upsert_document(self, doc: SourceDocument) -> None: ...

    @abstractmethod
    def get_document(self, doc_id: str) -> SourceDocument | None: ...

    @abstractmethod
    def list_documents(self, status: str | None = None) -> list[SourceDocument]: ...

    @abstractmethod
    def find_document_by_hash(self, content_hash: str) -> SourceDocument | None: ...

    # ------------------------------------------------------------------ chunks
    @abstractmethod
    def upsert_chunk(self, chunk: Chunk) -> None: ...

    @abstractmethod
    def get_chunk(self, chunk_id: str) -> Chunk | None: ...

    @abstractmethod
    def chunks_for_document(self, doc_id: str) -> list[Chunk]: ...

    @abstractmethod
    def remove_chunk(self, chunk_id: str) -> None: ...

    @abstractmethod
    def all_chunks(self) -> list[Chunk]: ...

    # ------------------------------------------------------------------ entities
    @abstractmethod
    def upsert_entity(self, entity: Entity) -> None: ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> Entity | None: ...

    @abstractmethod
    def remove_entity(self, entity_id: str) -> None: ...

    @abstractmethod
    def all_entities(self) -> list[Entity]: ...

    @abstractmethod
    def entities_mentioning(self, chunk_id: str) -> list[Entity]: ...

    @abstractmethod
    def search_entities(self, embedding: list[float], k: int) -> list[tuple[Entity, float]]:
        """Vector search over entity embeddings. Returns (entity, cosine) descending."""

    # ------------------------------------------------------------------ relations
    @abstractmethod
    def upsert_relation(self, relation: Relation) -> None: ...

    @abstractmethod
    def get_relation(self, relation_id: str) -> Relation | None: ...

    @abstractmethod
    def remove_relation(self, relation_id: str) -> None: ...

    @abstractmethod
    def all_relations(self) -> list[Relation]: ...

    @abstractmethod
    def relations_for_entity(self, entity_id: str) -> list[Relation]: ...

    @abstractmethod
    def relations_with_provenance(self, chunk_ids: set[str]) -> list[Relation]:
        """Every relation supported by at least one of these chunks."""

    # ------------------------------------------------------------------ communities
    @abstractmethod
    def upsert_community(self, community: Community) -> None: ...

    @abstractmethod
    def get_community(self, community_id: str) -> Community | None: ...

    @abstractmethod
    def remove_community(self, community_id: str) -> None: ...

    @abstractmethod
    def all_communities(self) -> list[Community]: ...

    @abstractmethod
    def dirty_communities(self, limit: int | None = None) -> list[Community]: ...

    # ------------------------------------------------------------------ audit trails
    @abstractmethod
    def log_resolution(self, decision: ResolutionDecision) -> None: ...

    @abstractmethod
    def list_resolutions(self, limit: int = 200) -> list[ResolutionDecision]: ...

    @abstractmethod
    def record_cost(self, record: CostRecord) -> None: ...

    @abstractmethod
    def list_costs(self, limit: int = 1000) -> list[CostRecord]: ...

    # ------------------------------------------------------------------ stats
    def stats(self) -> IndexStats:
        docs = self.list_documents()
        communities = self.all_communities()
        costs = self.list_costs(limit=1_000_000)
        return IndexStats(
            documents=len(docs),
            active_documents=sum(1 for d in docs if d.status == "active"),
            chunks=len(self.all_chunks()),
            entities=len(self.all_entities()),
            relations=len(self.all_relations()),
            communities=len(communities),
            dirty_communities=sum(1 for c in communities if c.dirty),
            total_tokens=sum(c.tokens for c in costs),
            total_usd=round(sum(c.usd for c in costs), 6),
        )
