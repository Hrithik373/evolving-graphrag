"""PostgreSQL-backed :class:`GraphStore`.

Why a relational store for a graph project: ArcadeDB is the only component of this system
with no managed offering anywhere, which ties deployment to a platform that will run
arbitrary Docker with a persistent volume. Postgres is free and managed on Neon, Supabase,
Render, Railway and everything else, so this backend is what makes the service deployable
rather than merely runnable.

It is not a compromise on the thesis. The central operation - "which relations does this
chunk support?" - is an array containment query against a **GIN index**, so retraction is
an index scan. The graph-database backend answers the same question through a collection
predicate whose support varies by version, and whose fallback is a client-side filter over
every relation. Provenance-first maintenance turns out to be a better fit for an inverted
index than for a graph traversal.

Two deliberate limitations, both shared with the ArcadeDB backend so the three stores stay
comparable:

* Vector search loads embeddings into an in-process :class:`VectorIndex` and refreshes the
  snapshot when the entity generation changes. ``pgvector`` is the drop-in upgrade and is
  available on every managed provider above; it is not used here so that all three backends
  rank identically and the parity tests mean something.
* Adjacency lives in array columns rather than a join table. The provenance sets are small
  and always read whole, so a join table would add work without adding an answer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
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
from egraph.store.vectors import VectorIndex

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parents[3] / "config" / "postgres_schema.sql"


def _rows_to(model, rows: list[dict[str, Any]]):
    return [model.model_validate(row) for row in rows]


class PostgresStore(GraphStore):
    def __init__(self, dsn: str, dim: int = 256, min_size: int = 1, max_size: int = 8) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.dsn = dsn
        self.dim = dim
        # A pool, because the api serves concurrent requests from a threadpool and the
        # worker runs jobs in parallel; a single shared connection would serialise both.
        self.pool = ConnectionPool(
            dsn, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row}, open=True
        )
        self._entity_index = VectorIndex(dim)
        self._index_generation: tuple[int, Any] | None = None

    # ------------------------------------------------------------------ plumbing
    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def _execute(self, sql: str, params: tuple = ()) -> int:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    # ------------------------------------------------------------------ lifecycle
    def migrate(self) -> None:
        if not SCHEMA_FILE.exists():  # installed as a wheel without the config dir
            log.warning("no postgres schema at %s; assuming it is already applied", SCHEMA_FILE)
            return
        # Strip comments before splitting on the terminator: a comment containing a
        # semicolon would otherwise be cut in half and its tail parsed as SQL.
        body = "\n".join(
            line
            for line in SCHEMA_FILE.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("--")
        )
        with self.pool.connection() as conn, conn.cursor() as cur:
            for chunk in body.split(";"):
                statement = chunk.strip()
                if statement:
                    cur.execute(statement)
        log.info("postgres schema applied")

    def ping(self) -> bool:
        try:
            return self._query("SELECT 1 AS ok")[0]["ok"] == 1
        except Exception:  # noqa: BLE001 - readiness probe must never raise
            return False

    def clear(self) -> None:
        self._execute(
            "TRUNCATE documents, chunks, entities, relations, communities, "
            "resolution_decisions, cost_records RESTART IDENTITY"
        )
        self._entity_index.clear()
        self._index_generation = None

    def close(self) -> None:
        self.pool.close()

    # ------------------------------------------------------------------ documents
    def upsert_document(self, doc: SourceDocument) -> None:
        self._execute(
            """
            INSERT INTO documents
                (doc_id, uri, content_hash, version, status, text, ingested_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (doc_id) DO UPDATE SET
                uri = EXCLUDED.uri,
                content_hash = EXCLUDED.content_hash,
                version = EXCLUDED.version,
                status = EXCLUDED.status,
                text = EXCLUDED.text,
                updated_at = EXCLUDED.updated_at
            """,
            (
                doc.doc_id,
                doc.uri,
                doc.content_hash,
                doc.version,
                doc.status,
                doc.text,
                doc.ingested_at,
                doc.updated_at,
            ),
        )

    def get_document(self, doc_id: str) -> SourceDocument | None:
        rows = self._query("SELECT * FROM documents WHERE doc_id = %s", (doc_id,))
        return SourceDocument.model_validate(rows[0]) if rows else None

    def list_documents(self, status: str | None = None) -> list[SourceDocument]:
        if status:
            rows = self._query(
                "SELECT * FROM documents WHERE status = %s ORDER BY doc_id", (status,)
            )
        else:
            rows = self._query("SELECT * FROM documents ORDER BY doc_id")
        return _rows_to(SourceDocument, rows)

    def find_document_by_hash(self, content_hash: str) -> SourceDocument | None:
        rows = self._query(
            "SELECT * FROM documents WHERE content_hash = %s AND status = 'active' LIMIT 1",
            (content_hash,),
        )
        return SourceDocument.model_validate(rows[0]) if rows else None

    # ------------------------------------------------------------------ chunks
    def upsert_chunk(self, chunk: Chunk) -> None:
        self._execute(
            """
            INSERT INTO chunks (chunk_id, doc_id, text, position, content_hash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (chunk_id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                text = EXCLUDED.text,
                position = EXCLUDED.position,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding
            """,
            (
                chunk.chunk_id,
                chunk.doc_id,
                chunk.text,
                chunk.position,
                chunk.content_hash,
                list(chunk.embedding),
            ),
        )

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        rows = self._query("SELECT * FROM chunks WHERE chunk_id = %s", (chunk_id,))
        return Chunk.model_validate(rows[0]) if rows else None

    def chunks_for_document(self, doc_id: str) -> list[Chunk]:
        return _rows_to(
            Chunk,
            self._query("SELECT * FROM chunks WHERE doc_id = %s ORDER BY position", (doc_id,)),
        )

    def remove_chunk(self, chunk_id: str) -> None:
        # Detach the mention before dropping the chunk, so no entity is left citing it.
        self._execute(
            "UPDATE entities SET mentions = array_remove(mentions, %s) WHERE %s = ANY(mentions)",
            (chunk_id, chunk_id),
        )
        self._execute("DELETE FROM chunks WHERE chunk_id = %s", (chunk_id,))

    def all_chunks(self) -> list[Chunk]:
        return _rows_to(Chunk, self._query("SELECT * FROM chunks"))

    # ------------------------------------------------------------------ entities
    def upsert_entity(self, entity: Entity) -> None:
        self._execute(
            """
            INSERT INTO entities (entity_id, canonical_name, type, description, descriptions,
                                  embedding, degree, community_id, mentions, aliases,
                                  created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (entity_id) DO UPDATE SET
                canonical_name = EXCLUDED.canonical_name,
                type = EXCLUDED.type,
                description = EXCLUDED.description,
                descriptions = EXCLUDED.descriptions,
                embedding = EXCLUDED.embedding,
                degree = EXCLUDED.degree,
                community_id = EXCLUDED.community_id,
                mentions = EXCLUDED.mentions,
                aliases = EXCLUDED.aliases,
                updated_at = EXCLUDED.updated_at
            """,
            (
                entity.entity_id,
                entity.canonical_name,
                entity.type,
                entity.description,
                json.dumps(entity.descriptions),
                list(entity.embedding),
                entity.degree,
                entity.community_id,
                sorted(entity.mentions),
                sorted(entity.aliases),
                entity.created_at,
                entity.updated_at,
            ),
        )
        self._index_generation = None

    def get_entity(self, entity_id: str) -> Entity | None:
        rows = self._query("SELECT * FROM entities WHERE entity_id = %s", (entity_id,))
        return Entity.model_validate(rows[0]) if rows else None

    def remove_entity(self, entity_id: str) -> None:
        # A relation whose endpoint is gone would be dangling, so it goes with the entity.
        self._execute(
            "DELETE FROM relations WHERE source_id = %s OR target_id = %s", (entity_id, entity_id)
        )
        self._execute(
            "UPDATE communities SET members = array_remove(members, %s), "
            "member_count = cardinality(array_remove(members, %s)) WHERE %s = ANY(members)",
            (entity_id, entity_id, entity_id),
        )
        self._execute("DELETE FROM entities WHERE entity_id = %s", (entity_id,))
        self._index_generation = None

    def all_entities(self) -> list[Entity]:
        return _rows_to(Entity, self._query("SELECT * FROM entities"))

    def entities_mentioning(self, chunk_id: str) -> list[Entity]:
        # Array containment against the GIN index on `mentions`.
        return _rows_to(
            Entity,
            self._query(
                "SELECT * FROM entities WHERE mentions @> ARRAY[%s]::text[] ORDER BY entity_id",
                (chunk_id,),
            ),
        )

    def search_entities(self, embedding: list[float], k: int) -> list[tuple[Entity, float]]:
        self._refresh_vector_index()
        out: list[tuple[Entity, float]] = []
        for entity_id, score in self._entity_index.search(embedding, k):
            entity = self.get_entity(entity_id)
            if entity is not None:
                out.append((entity, score))
        return out

    def _refresh_vector_index(self) -> None:
        """Rebuild the in-process vector snapshot when the entity set has moved on."""
        rows = self._query("SELECT count(*) AS n, max(updated_at) AS latest FROM entities")
        generation = (int(rows[0]["n"]), rows[0]["latest"])
        if generation == self._index_generation:
            return
        self._entity_index.clear()
        for row in self._query(
            "SELECT entity_id, embedding FROM entities WHERE cardinality(embedding) > 0"
        ):
            self._entity_index.upsert(row["entity_id"], row["embedding"])
        self._index_generation = generation

    # ------------------------------------------------------------------ relations
    def upsert_relation(self, relation: Relation) -> None:
        self._execute(
            """
            INSERT INTO relations (relation_id, source_id, target_id, relation_type,
                                   description, descriptions, weight, provenance, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (relation_id) DO UPDATE SET
                source_id = EXCLUDED.source_id,
                target_id = EXCLUDED.target_id,
                relation_type = EXCLUDED.relation_type,
                description = EXCLUDED.description,
                descriptions = EXCLUDED.descriptions,
                weight = EXCLUDED.weight,
                provenance = EXCLUDED.provenance,
                updated_at = EXCLUDED.updated_at
            """,
            (
                relation.relation_id,
                relation.source_id,
                relation.target_id,
                relation.relation_type,
                relation.description,
                json.dumps(relation.descriptions),
                relation.weight,
                sorted(relation.provenance),
                relation.updated_at,
            ),
        )

    def get_relation(self, relation_id: str) -> Relation | None:
        rows = self._query("SELECT * FROM relations WHERE relation_id = %s", (relation_id,))
        return Relation.model_validate(rows[0]) if rows else None

    def remove_relation(self, relation_id: str) -> None:
        self._execute("DELETE FROM relations WHERE relation_id = %s", (relation_id,))

    def all_relations(self) -> list[Relation]:
        return _rows_to(Relation, self._query("SELECT * FROM relations"))

    def relations_for_entity(self, entity_id: str) -> list[Relation]:
        return _rows_to(
            Relation,
            self._query(
                "SELECT * FROM relations WHERE source_id = %s OR target_id = %s "
                "ORDER BY relation_id",
                (entity_id, entity_id),
            ),
        )

    def relations_with_provenance(self, chunk_ids: set[str]) -> list[Relation]:
        """The retraction lever, as one indexed query.

        ``provenance && ARRAY[...]`` is array overlap against the GIN index, so this asks
        the database "which relations does any of these chunks support?" in a single scan
        of the index rather than of the table.
        """
        if not chunk_ids:
            return []
        return _rows_to(
            Relation,
            self._query(
                "SELECT * FROM relations WHERE provenance && %s::text[] ORDER BY relation_id",
                (sorted(chunk_ids),),
            ),
        )

    # ------------------------------------------------------------------ communities
    def upsert_community(self, community: Community) -> None:
        community.member_count = len(community.members)
        self._execute(
            """
            INSERT INTO communities (community_id, level, title, summary, summary_hash, dirty,
                                     members, summarized_members, member_count, embedding,
                                     dirty_since, dirty_seq, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (community_id) DO UPDATE SET
                level = EXCLUDED.level,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                summary_hash = EXCLUDED.summary_hash,
                dirty = EXCLUDED.dirty,
                members = EXCLUDED.members,
                summarized_members = EXCLUDED.summarized_members,
                member_count = EXCLUDED.member_count,
                embedding = EXCLUDED.embedding,
                dirty_since = EXCLUDED.dirty_since,
                dirty_seq = EXCLUDED.dirty_seq,
                updated_at = EXCLUDED.updated_at
            """,
            (
                community.community_id,
                community.level,
                community.title,
                community.summary,
                community.summary_hash,
                community.dirty,
                sorted(community.members),
                sorted(community.summarized_members),
                community.member_count,
                list(community.embedding),
                community.dirty_since,
                community.dirty_seq,
                community.updated_at,
            ),
        )

    def get_community(self, community_id: str) -> Community | None:
        rows = self._query("SELECT * FROM communities WHERE community_id = %s", (community_id,))
        return Community.model_validate(rows[0]) if rows else None

    def remove_community(self, community_id: str) -> None:
        self._execute(
            "UPDATE entities SET community_id = NULL WHERE community_id = %s", (community_id,)
        )
        self._execute("DELETE FROM communities WHERE community_id = %s", (community_id,))

    def all_communities(self) -> list[Community]:
        return _rows_to(Community, self._query("SELECT * FROM communities"))

    def dirty_communities(self, limit: int | None = None) -> list[Community]:
        sql = "SELECT * FROM communities WHERE dirty ORDER BY dirty_seq, community_id"
        params: tuple = ()
        if limit:
            sql += " LIMIT %s"
            params = (limit,)
        return _rows_to(Community, self._query(sql, params))

    # ------------------------------------------------------------------ audit trails
    def log_resolution(self, decision: ResolutionDecision) -> None:
        self._execute(
            """
            INSERT INTO resolution_decisions (candidate_name, candidate_type, decision,
                matched_entity_id, similarity, name_match, reason, chunk_id, at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision.candidate_name,
                decision.candidate_type,
                decision.decision,
                decision.matched_entity_id,
                decision.similarity,
                decision.name_match,
                decision.reason,
                decision.chunk_id,
                decision.at,
            ),
        )

    def list_resolutions(self, limit: int = 200) -> list[ResolutionDecision]:
        rows = self._query(
            "SELECT * FROM (SELECT * FROM resolution_decisions ORDER BY id DESC LIMIT %s) t "
            "ORDER BY id",
            (limit,),
        )
        return _rows_to(ResolutionDecision, rows)

    def record_cost(self, record: CostRecord) -> None:
        self._execute(
            """
            INSERT INTO cost_records (operation, tokens_in, tokens_out, wall_ms, usd, model,
                                      cache_hit, doc_id, community_id, at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.operation,
                record.tokens_in,
                record.tokens_out,
                record.wall_ms,
                record.usd,
                record.model,
                record.cache_hit,
                record.doc_id,
                record.community_id,
                record.at,
            ),
        )

    def list_costs(self, limit: int = 1000) -> list[CostRecord]:
        rows = self._query(
            "SELECT * FROM (SELECT * FROM cost_records ORDER BY id DESC LIMIT %s) t ORDER BY id",
            (limit,),
        )
        return _rows_to(CostRecord, rows)

    # ------------------------------------------------------------------ stats
    def stats(self):
        """One round trip instead of the base class's five table scans."""
        from egraph.schemas import IndexStats

        row = self._query("""
            SELECT
                (SELECT count(*) FROM documents) AS documents,
                (SELECT count(*) FROM documents WHERE status = 'active') AS active_documents,
                (SELECT count(*) FROM chunks) AS chunks,
                (SELECT count(*) FROM entities) AS entities,
                (SELECT count(*) FROM relations) AS relations,
                (SELECT count(*) FROM communities) AS communities,
                (SELECT count(*) FROM communities WHERE dirty) AS dirty_communities,
                (SELECT coalesce(sum(tokens_in + tokens_out), 0) FROM cost_records) AS total_tokens,
                (SELECT coalesce(sum(usd), 0) FROM cost_records) AS total_usd
            """)[0]
        return IndexStats(
            documents=row["documents"],
            active_documents=row["active_documents"],
            chunks=row["chunks"],
            entities=row["entities"],
            relations=row["relations"],
            communities=row["communities"],
            dirty_communities=row["dirty_communities"],
            total_tokens=int(row["total_tokens"]),
            total_usd=round(float(row["total_usd"]), 6),
        )

