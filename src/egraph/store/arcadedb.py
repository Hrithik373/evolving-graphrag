"""ArcadeDB-backed :class:`GraphStore` - graph and vectors in one store.

Two honest notes about this backend:

1. ArcadeDB's HNSW vector index is reachable from its Java API, not from SQL. So entity
   vector search here loads embeddings over SQL into a :class:`VectorIndex` and refreshes
   that snapshot when the entity generation changes. At this project's corpus sizes that
   is fast and exactly reproducible; the swap-in point for a real ANN index is one method.
2. Adjacency is stored twice: as real graph edges (MENTIONS / RELATES_TO / IN_COMMUNITY /
   PART_OF, so the database really is a graph and is explorable in ArcadeDB Studio) and as
   list properties on the vertices (so the provenance walks are single indexed queries).
   The list properties are the read path; the edges are the graph of record.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

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
from egraph.store.client import ArcadeDBClient, ArcadeDBError
from egraph.store.schema import migrate as apply_schema
from egraph.store.vectors import VectorIndex

log = logging.getLogger(__name__)

_SYSTEM_FIELDS = {"@rid", "@type", "@cat", "@in", "@out"}


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _SYSTEM_FIELDS}


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class ArcadeDBStore(GraphStore):
    def __init__(self, client: ArcadeDBClient, dim: int = 256) -> None:
        self.client = client
        self.dim = dim
        self._entity_index = VectorIndex(dim)
        self._index_generation = -1
        self._supports_in_operator: bool | None = None
        self._supports_edge_if_not_exists: bool | None = None

    # ------------------------------------------------------------------ lifecycle
    def migrate(self) -> None:
        apply_schema(self.client)

    def ping(self) -> bool:
        return self.client.ping()

    def clear(self) -> None:
        # Edges first, then vertices: DELETE VERTEX cleans up incident edges, but dropping
        # the edge types explicitly keeps the database clean even if a vertex type is
        # already empty.
        for etype in ("RELATES_TO", "MENTIONS", "IN_COMMUNITY", "PART_OF"):
            self.client.command(f"DELETE FROM {etype} UNSAFE")
        for vtype in ("Chunk", "Entity", "Community", "SourceDocument"):
            self.client.command(f"DELETE VERTEX {vtype}")
        for dtype in ("ResolutionDecision", "CostRecord"):
            self.client.command(f"DELETE FROM {dtype}")
        self._entity_index.clear()
        self._index_generation = -1

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------ helpers
    def _upsert_vertex(self, vtype: str, key_field: str, key: str, data: dict[str, Any]) -> None:
        assignments = ", ".join(f"{k} = :{k}" for k in data)
        params = {k: _iso(v) for k, v in data.items()}
        params[key_field] = key
        self.client.command(
            f"UPDATE {vtype} SET {assignments} UPSERT WHERE {key_field} = :{key_field}", params
        )

    def _exists(self, vtype: str, field: str, value: str) -> bool:
        rows = self.client.query(
            f"SELECT count(*) AS n FROM {vtype} WHERE {field} = :v LIMIT 1", {"v": value}
        )
        return bool(rows) and int(rows[0].get("n", 0)) > 0

    def _ensure_edge(
        self,
        etype: str,
        from_type: str,
        from_field: str,
        from_id: str,
        to_type: str,
        to_field: str,
        to_id: str,
    ) -> None:
        """Create an edge unless one of this type already joins the two vertices.

        ``CREATE EDGE ... IF NOT EXISTS`` is the one-statement form, but support for it
        varies by ArcadeDB version, so the first rejection switches this permanently to an
        explicit check-then-create. Duplicate MENTIONS edges would not corrupt provenance
        (the list properties are the read path) but they would quietly inflate the graph.
        """
        params = {"a": from_id, "b": to_id}
        create = (
            f"CREATE EDGE {etype} "
            f"FROM (SELECT FROM {from_type} WHERE {from_field} = :a) "
            f"TO (SELECT FROM {to_type} WHERE {to_field} = :b)"
        )
        if self._supports_edge_if_not_exists is not False:
            try:
                self.client.command(f"{create} IF NOT EXISTS", params)
                self._supports_edge_if_not_exists = True
                return
            except ArcadeDBError:
                self._supports_edge_if_not_exists = False
                log.info("ArcadeDB rejected CREATE EDGE IF NOT EXISTS; using check-then-create")

        existing = self.client.query(
            f"SELECT count(*) AS n FROM {etype} "
            f"WHERE out.{from_field} = :a AND in.{to_field} = :b",
            params,
        )
        if existing and int(existing[0].get("n", 0)) > 0:
            return
        self.client.command(create, params)

    def _contains(self, vtype: str, field: str, value: str) -> list[dict[str, Any]]:
        """`WHERE <value> IN <list field>`, with a client-side fallback if the server
        rejects the operator (ArcadeDB versions differ on collection predicates)."""
        if self._supports_in_operator is not False:
            try:
                rows = self.client.query(
                    f"SELECT FROM {vtype} WHERE :needle IN {field}", {"needle": value}
                )
                self._supports_in_operator = True
                return rows
            except ArcadeDBError:
                self._supports_in_operator = False
                log.warning("ArcadeDB rejected IN-on-collection; falling back to client filter")
        rows = self.client.query(f"SELECT FROM {vtype}")
        return [r for r in rows if value in (r.get(field) or [])]

    # ------------------------------------------------------------------ documents
    def upsert_document(self, doc: SourceDocument) -> None:
        self._upsert_vertex("SourceDocument", "doc_id", doc.doc_id, doc.model_dump(mode="json"))

    def get_document(self, doc_id: str) -> SourceDocument | None:
        rows = self.client.query(
            "SELECT FROM SourceDocument WHERE doc_id = :doc_id", {"doc_id": doc_id}
        )
        return SourceDocument.model_validate(_clean(rows[0])) if rows else None

    def list_documents(self, status: str | None = None) -> list[SourceDocument]:
        sql = "SELECT FROM SourceDocument"
        params: dict[str, Any] = {}
        if status:
            sql += " WHERE status = :status"
            params["status"] = status
        sql += " ORDER BY doc_id"
        return [SourceDocument.model_validate(_clean(r)) for r in self.client.query(sql, params)]

    def find_document_by_hash(self, content_hash: str) -> SourceDocument | None:
        rows = self.client.query(
            "SELECT FROM SourceDocument WHERE content_hash = :h AND status = 'active' LIMIT 1",
            {"h": content_hash},
        )
        return SourceDocument.model_validate(_clean(rows[0])) if rows else None

    # ------------------------------------------------------------------ chunks
    def upsert_chunk(self, chunk: Chunk) -> None:
        self._upsert_vertex("Chunk", "chunk_id", chunk.chunk_id, chunk.model_dump(mode="json"))
        self._ensure_edge(
            "PART_OF",
            "Chunk",
            "chunk_id",
            chunk.chunk_id,
            "SourceDocument",
            "doc_id",
            chunk.doc_id,
        )

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        rows = self.client.query("SELECT FROM Chunk WHERE chunk_id = :c", {"c": chunk_id})
        return Chunk.model_validate(_clean(rows[0])) if rows else None

    def chunks_for_document(self, doc_id: str) -> list[Chunk]:
        rows = self.client.query(
            "SELECT FROM Chunk WHERE doc_id = :d ORDER BY position", {"d": doc_id}
        )
        return [Chunk.model_validate(_clean(r)) for r in rows]

    def remove_chunk(self, chunk_id: str) -> None:
        for entity in self.entities_mentioning(chunk_id):
            entity.mentions.discard(chunk_id)
            self.upsert_entity(entity)
        self.client.command("DELETE VERTEX Chunk WHERE chunk_id = :c", {"c": chunk_id})

    def all_chunks(self) -> list[Chunk]:
        return [Chunk.model_validate(_clean(r)) for r in self.client.query("SELECT FROM Chunk")]

    # ------------------------------------------------------------------ entities
    def upsert_entity(self, entity: Entity) -> None:
        data = entity.model_dump(mode="json")
        data["mentions"] = sorted(entity.mentions)
        data["aliases"] = sorted(entity.aliases)
        self._upsert_vertex("Entity", "entity_id", entity.entity_id, data)
        self._index_generation = -1
        for chunk_id in entity.mentions:
            self._ensure_edge(
                "MENTIONS",
                "Chunk",
                "chunk_id",
                chunk_id,
                "Entity",
                "entity_id",
                entity.entity_id,
            )
        if entity.community_id and self._exists("Community", "community_id", entity.community_id):
            self._ensure_edge(
                "IN_COMMUNITY",
                "Entity",
                "entity_id",
                entity.entity_id,
                "Community",
                "community_id",
                entity.community_id,
            )

    def get_entity(self, entity_id: str) -> Entity | None:
        rows = self.client.query("SELECT FROM Entity WHERE entity_id = :e", {"e": entity_id})
        return Entity.model_validate(_clean(rows[0])) if rows else None

    def remove_entity(self, entity_id: str) -> None:
        for relation in self.relations_for_entity(entity_id):
            self.remove_relation(relation.relation_id)
        self.client.command("DELETE VERTEX Entity WHERE entity_id = :e", {"e": entity_id})
        self._index_generation = -1

    def all_entities(self) -> list[Entity]:
        return [Entity.model_validate(_clean(r)) for r in self.client.query("SELECT FROM Entity")]

    def entities_mentioning(self, chunk_id: str) -> list[Entity]:
        rows = self._contains("Entity", "mentions", chunk_id)
        return [Entity.model_validate(_clean(r)) for r in rows]

    def search_entities(self, embedding: list[float], k: int) -> list[tuple[Entity, float]]:
        self._refresh_vector_index()
        hits = self._entity_index.search(embedding, k)
        out: list[tuple[Entity, float]] = []
        for entity_id, score in hits:
            entity = self.get_entity(entity_id)
            if entity is not None:
                out.append((entity, score))
        return out

    def _refresh_vector_index(self) -> None:
        rows = self.client.query("SELECT count(*) as n FROM Entity")
        generation = int(rows[0]["n"]) if rows else 0
        if generation == self._index_generation:
            return
        self._entity_index.clear()
        for row in self.client.query("SELECT entity_id, embedding FROM Entity"):
            if row.get("embedding"):
                self._entity_index.upsert(row["entity_id"], row["embedding"])
        self._index_generation = generation

    # ------------------------------------------------------------------ relations
    def upsert_relation(self, relation: Relation) -> None:
        data = relation.model_dump(mode="json")
        data["provenance"] = sorted(relation.provenance)
        assignments = ", ".join(f"{k} = :{k}" for k in data)
        params = {k: _iso(v) for k, v in data.items()}
        # Check-then-write rather than `UPDATE ... RETURN AFTER`: the return-clause form is
        # not portable across ArcadeDB versions, and this path runs on every assertion.
        if self._exists("RELATES_TO", "relation_id", relation.relation_id):
            self.client.command(
                f"UPDATE RELATES_TO SET {assignments} WHERE relation_id = :relation_id", params
            )
            return
        self.client.command(
            "CREATE EDGE RELATES_TO FROM (SELECT FROM Entity WHERE entity_id = :source_id) "
            f"TO (SELECT FROM Entity WHERE entity_id = :target_id) SET {assignments}",
            params,
        )

    def get_relation(self, relation_id: str) -> Relation | None:
        rows = self.client.query(
            "SELECT FROM RELATES_TO WHERE relation_id = :r", {"r": relation_id}
        )
        return Relation.model_validate(_clean(rows[0])) if rows else None

    def remove_relation(self, relation_id: str) -> None:
        self.client.command("DELETE EDGE RELATES_TO WHERE relation_id = :r", {"r": relation_id})

    def all_relations(self) -> list[Relation]:
        rows = self.client.query("SELECT FROM RELATES_TO")
        return [Relation.model_validate(_clean(r)) for r in rows]

    def relations_for_entity(self, entity_id: str) -> list[Relation]:
        rows = self.client.query(
            "SELECT FROM RELATES_TO WHERE source_id = :e OR target_id = :e", {"e": entity_id}
        )
        return [Relation.model_validate(_clean(r)) for r in rows]

    def relations_with_provenance(self, chunk_ids: set[str]) -> list[Relation]:
        found: dict[str, Relation] = {}
        for chunk_id in sorted(chunk_ids):
            for row in self._contains("RELATES_TO", "provenance", chunk_id):
                relation = Relation.model_validate(_clean(row))
                found[relation.relation_id] = relation
        return [found[rid] for rid in sorted(found)]

    # ------------------------------------------------------------------ communities
    def upsert_community(self, community: Community) -> None:
        community.member_count = len(community.members)
        data = community.model_dump(mode="json")
        data["members"] = sorted(community.members)
        data["summarized_members"] = sorted(community.summarized_members)
        self._upsert_vertex("Community", "community_id", community.community_id, data)

    def get_community(self, community_id: str) -> Community | None:
        rows = self.client.query(
            "SELECT FROM Community WHERE community_id = :k", {"k": community_id}
        )
        return Community.model_validate(_clean(rows[0])) if rows else None

    def remove_community(self, community_id: str) -> None:
        self.client.command(
            "UPDATE Entity SET community_id = null WHERE community_id = :k", {"k": community_id}
        )
        self.client.command("DELETE VERTEX Community WHERE community_id = :k", {"k": community_id})

    def all_communities(self) -> list[Community]:
        rows = self.client.query("SELECT FROM Community")
        return [Community.model_validate(_clean(r)) for r in rows]

    def dirty_communities(self, limit: int | None = None) -> list[Community]:
        sql = "SELECT FROM Community WHERE dirty = true ORDER BY dirty_seq, community_id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [Community.model_validate(_clean(r)) for r in self.client.query(sql)]

    # ------------------------------------------------------------------ audit trails
    def log_resolution(self, decision: ResolutionDecision) -> None:
        data = decision.model_dump(mode="json")
        fields = ", ".join(f"{k} = :{k}" for k in data)
        self.client.command(f"INSERT INTO ResolutionDecision SET {fields}", data)

    def list_resolutions(self, limit: int = 200) -> list[ResolutionDecision]:
        rows = self.client.query(
            f"SELECT FROM ResolutionDecision ORDER BY at DESC LIMIT {int(limit)}"
        )
        return [ResolutionDecision.model_validate(_clean(r)) for r in rows]

    def record_cost(self, record: CostRecord) -> None:
        data = record.model_dump(mode="json")
        fields = ", ".join(f"{k} = :{k}" for k in data)
        self.client.command(f"INSERT INTO CostRecord SET {fields}", data)

    def list_costs(self, limit: int = 1000) -> list[CostRecord]:
        rows = self.client.query(f"SELECT FROM CostRecord ORDER BY at DESC LIMIT {int(limit)}")
        return [CostRecord.model_validate(_clean(r)) for r in rows]
