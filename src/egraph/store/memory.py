"""In-process reference implementation of :class:`GraphStore`.

This is the store the tests, the offline demo and the eval harness run against: no daemon,
no network, fully deterministic. It is also the semantics oracle - ``ArcadeDBStore`` must
behave identically, and ``tests/test_store_parity.py`` checks that when ArcadeDB is up.

Optionally snapshots itself to JSON so a `make demo` survives a process restart.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

from egraph.schemas import (
    Chunk,
    Community,
    CostRecord,
    Entity,
    Relation,
    ResolutionDecision,
    SourceDocument,
)
from egraph.store.base import GraphStore
from egraph.store.vectors import VectorIndex


class MemoryStore(GraphStore):
    def __init__(self, dim: int = 256, persist_path: str | None = None) -> None:
        self.dim = dim
        self.persist_path = Path(persist_path) if persist_path else None
        self._lock = threading.RLock()
        self._docs: dict[str, SourceDocument] = {}
        self._chunks: dict[str, Chunk] = {}
        self._chunks_by_doc: dict[str, set[str]] = defaultdict(set)
        self._entities: dict[str, Entity] = {}
        self._mentions: dict[str, set[str]] = defaultdict(set)  # chunk_id -> entity_ids
        self._relations: dict[str, Relation] = {}
        self._rel_by_entity: dict[str, set[str]] = defaultdict(set)
        self._rel_by_chunk: dict[str, set[str]] = defaultdict(set)
        self._communities: dict[str, Community] = {}
        self._resolutions: list[ResolutionDecision] = []
        self._costs: list[CostRecord] = []
        self._entity_index = VectorIndex(dim)
        if self.persist_path and self.persist_path.exists():
            self._load()

    # ------------------------------------------------------------------ lifecycle
    def migrate(self) -> None:
        return None

    def ping(self) -> bool:
        return True

    def clear(self) -> None:
        with self._lock:
            self._docs.clear()
            self._chunks.clear()
            self._chunks_by_doc.clear()
            self._entities.clear()
            self._mentions.clear()
            self._relations.clear()
            self._rel_by_entity.clear()
            self._rel_by_chunk.clear()
            self._communities.clear()
            self._resolutions.clear()
            self._costs.clear()
            self._entity_index.clear()

    # ------------------------------------------------------------------ documents
    def upsert_document(self, doc: SourceDocument) -> None:
        with self._lock:
            self._docs[doc.doc_id] = doc

    def get_document(self, doc_id: str) -> SourceDocument | None:
        return self._docs.get(doc_id)

    def list_documents(self, status: str | None = None) -> list[SourceDocument]:
        docs = list(self._docs.values())
        if status:
            docs = [d for d in docs if d.status == status]
        return sorted(docs, key=lambda d: d.doc_id)

    def find_document_by_hash(self, content_hash: str) -> SourceDocument | None:
        for doc in self._docs.values():
            if doc.content_hash == content_hash and doc.status == "active":
                return doc
        return None

    # ------------------------------------------------------------------ chunks
    def upsert_chunk(self, chunk: Chunk) -> None:
        with self._lock:
            self._chunks[chunk.chunk_id] = chunk
            self._chunks_by_doc[chunk.doc_id].add(chunk.chunk_id)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def chunks_for_document(self, doc_id: str) -> list[Chunk]:
        ids = self._chunks_by_doc.get(doc_id, set())
        return sorted((self._chunks[i] for i in ids if i in self._chunks), key=lambda c: c.position)

    def remove_chunk(self, chunk_id: str) -> None:
        with self._lock:
            chunk = self._chunks.pop(chunk_id, None)
            if chunk is None:
                return
            self._chunks_by_doc[chunk.doc_id].discard(chunk_id)
            for entity_id in list(self._mentions.pop(chunk_id, set())):
                entity = self._entities.get(entity_id)
                if entity is not None:
                    entity.mentions.discard(chunk_id)
            self._rel_by_chunk.pop(chunk_id, None)

    def all_chunks(self) -> list[Chunk]:
        return list(self._chunks.values())

    # ------------------------------------------------------------------ entities
    def upsert_entity(self, entity: Entity) -> None:
        with self._lock:
            previous = self._entities.get(entity.entity_id)
            if previous is not None:
                for chunk_id in previous.mentions - entity.mentions:
                    self._mentions[chunk_id].discard(entity.entity_id)
            self._entities[entity.entity_id] = entity
            for chunk_id in entity.mentions:
                self._mentions[chunk_id].add(entity.entity_id)
            if entity.embedding:
                self._entity_index.upsert(entity.entity_id, entity.embedding)

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def remove_entity(self, entity_id: str) -> None:
        with self._lock:
            entity = self._entities.pop(entity_id, None)
            if entity is None:
                return
            for chunk_id in entity.mentions:
                self._mentions[chunk_id].discard(entity_id)
            for relation_id in list(self._rel_by_entity.pop(entity_id, set())):
                self.remove_relation(relation_id)
            self._entity_index.remove(entity_id)
            if entity.community_id:
                community = self._communities.get(entity.community_id)
                if community is not None:
                    community.members.discard(entity_id)
                    community.member_count = len(community.members)

    def all_entities(self) -> list[Entity]:
        return list(self._entities.values())

    def entities_mentioning(self, chunk_id: str) -> list[Entity]:
        return [
            self._entities[e]
            for e in sorted(self._mentions.get(chunk_id, set()))
            if e in self._entities
        ]

    def search_entities(self, embedding: list[float], k: int) -> list[tuple[Entity, float]]:
        hits = self._entity_index.search(embedding, k)
        out = []
        for entity_id, score in hits:
            entity = self._entities.get(entity_id)
            if entity is not None:
                out.append((entity, score))
        return out

    # ------------------------------------------------------------------ relations
    def upsert_relation(self, relation: Relation) -> None:
        with self._lock:
            previous = self._relations.get(relation.relation_id)
            if previous is not None:
                for chunk_id in previous.provenance - relation.provenance:
                    self._rel_by_chunk[chunk_id].discard(relation.relation_id)
            self._relations[relation.relation_id] = relation
            self._rel_by_entity[relation.source_id].add(relation.relation_id)
            self._rel_by_entity[relation.target_id].add(relation.relation_id)
            for chunk_id in relation.provenance:
                self._rel_by_chunk[chunk_id].add(relation.relation_id)

    def get_relation(self, relation_id: str) -> Relation | None:
        return self._relations.get(relation_id)

    def remove_relation(self, relation_id: str) -> None:
        with self._lock:
            relation = self._relations.pop(relation_id, None)
            if relation is None:
                return
            self._rel_by_entity[relation.source_id].discard(relation_id)
            self._rel_by_entity[relation.target_id].discard(relation_id)
            for chunk_id in relation.provenance:
                self._rel_by_chunk[chunk_id].discard(relation_id)

    def all_relations(self) -> list[Relation]:
        return list(self._relations.values())

    def relations_for_entity(self, entity_id: str) -> list[Relation]:
        return [
            self._relations[r]
            for r in sorted(self._rel_by_entity.get(entity_id, set()))
            if r in self._relations
        ]

    def relations_with_provenance(self, chunk_ids: set[str]) -> list[Relation]:
        found: dict[str, Relation] = {}
        for chunk_id in sorted(chunk_ids):
            for relation_id in sorted(self._rel_by_chunk.get(chunk_id, set())):
                relation = self._relations.get(relation_id)
                if relation is not None:
                    found[relation_id] = relation
        return list(found.values())

    # ------------------------------------------------------------------ communities
    def upsert_community(self, community: Community) -> None:
        with self._lock:
            community.member_count = len(community.members)
            self._communities[community.community_id] = community

    def get_community(self, community_id: str) -> Community | None:
        return self._communities.get(community_id)

    def remove_community(self, community_id: str) -> None:
        with self._lock:
            community = self._communities.pop(community_id, None)
            if community is None:
                return
            for entity_id in community.members:
                entity = self._entities.get(entity_id)
                if entity is not None and entity.community_id == community_id:
                    entity.community_id = None

    def all_communities(self) -> list[Community]:
        return list(self._communities.values())

    def dirty_communities(self, limit: int | None = None) -> list[Community]:
        dirty = [c for c in self._communities.values() if c.dirty]
        # Oldest-dirty-first by an explicit total order, so no community can starve behind
        # a hot one and the batch a bounded budget picks is reproducible.
        dirty.sort(key=lambda c: (c.dirty_seq, c.community_id))
        return dirty[:limit] if limit else dirty

    # ------------------------------------------------------------------ audit trails
    def log_resolution(self, decision: ResolutionDecision) -> None:
        self._resolutions.append(decision)

    def list_resolutions(self, limit: int = 200) -> list[ResolutionDecision]:
        return self._resolutions[-limit:]

    def record_cost(self, record: CostRecord) -> None:
        self._costs.append(record)

    def list_costs(self, limit: int = 1000) -> list[CostRecord]:
        return self._costs[-limit:]

    # ------------------------------------------------------------------ persistence
    def save(self) -> None:
        if not self.persist_path:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "documents": [d.model_dump(mode="json") for d in self._docs.values()],
            "chunks": [c.model_dump(mode="json") for c in self._chunks.values()],
            "entities": [_dump_entity(e) for e in self._entities.values()],
            "relations": [_dump_relation(r) for r in self._relations.values()],
            "communities": [_dump_community(c) for c in self._communities.values()],
            "costs": [c.model_dump(mode="json") for c in self._costs],
            "resolutions": [r.model_dump(mode="json") for r in self._resolutions],
        }
        tmp = self.persist_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.persist_path)

    def _load(self) -> None:
        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for raw in payload.get("documents", []):
            self.upsert_document(SourceDocument.model_validate(raw))
        for raw in payload.get("chunks", []):
            self.upsert_chunk(Chunk.model_validate(raw))
        for raw in payload.get("entities", []):
            self.upsert_entity(Entity.model_validate(raw))
        for raw in payload.get("relations", []):
            self.upsert_relation(Relation.model_validate(raw))
        for raw in payload.get("communities", []):
            self.upsert_community(Community.model_validate(raw))
        for raw in payload.get("costs", []):
            self._costs.append(CostRecord.model_validate(raw))
        for raw in payload.get("resolutions", []):
            self._resolutions.append(ResolutionDecision.model_validate(raw))


def _dump_entity(entity: Entity) -> dict:
    data = entity.model_dump(mode="json")
    data["mentions"] = sorted(entity.mentions)
    data["aliases"] = sorted(entity.aliases)
    return data


def _dump_relation(relation: Relation) -> dict:
    data = relation.model_dump(mode="json")
    data["provenance"] = sorted(relation.provenance)
    return data


def _dump_community(community: Community) -> dict:
    data = community.model_dump(mode="json")
    data["members"] = sorted(community.members)
    data["summarized_members"] = sorted(community.summarized_members)
    return data
