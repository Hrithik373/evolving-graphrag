"""The churn engine - the front door for every write.

Split of work, and the reason for it:

    SYNC  (returns to the caller in milliseconds)
      - persist the document record and the new chunk set
      - diff against the previous chunk set
      - run provenance GC for the chunks that went away (pure graph work, no model calls)
      - mark the affected communities dirty
      - enqueue the expensive half

    ASYNC (a worker, via ``ingest_chunks``)
      - embed and extract the chunks that are actually new
      - resolve entities, upsert entities/relations with provenance
      - place new entities into communities, mark those dirty
      - hand the dirty set to the summary recomputer

Deletion is entirely synchronous on purpose: retraction never needs a model, so a DELETE
can return its full GC receipt in the response. That is both the demo and the argument -
removing a document from the index is a graph walk, not a rebuild.
"""

from __future__ import annotations

import logging
import time

from egraph.churn import gc
from egraph.churn import mutations as ops
from egraph.churn.diff import ChunkDiff, diff_chunks
from egraph.churn.dirty import DirtyTracker
from egraph.cluster.placement import place_all
from egraph.extract.entities import Extractor
from egraph.gateway.embeddings import Embedder
from egraph.ingest.chunker import chunk_document
from egraph.ingest.loader import is_noop, upsert_document_record
from egraph.ingest.resolver import EntityResolver
from egraph.observability import metrics
from egraph.schemas import (
    Chunk,
    ChurnResult,
    DocumentChange,
    GraphMutation,
    utcnow,
)
from egraph.settings import Settings, get_settings
from egraph.store.base import GraphStore

log = logging.getLogger(__name__)


class ChurnEngine:
    def __init__(
        self,
        store: GraphStore,
        embedder: Embedder,
        extractor: Extractor,
        resolver: EntityResolver | None = None,
        queue=None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.extractor = extractor
        self.settings = settings or get_settings()
        self.resolver = resolver or EntityResolver(store, self.settings)
        self.dirty = DirtyTracker(store)
        self.queue = queue

    # ------------------------------------------------------------------ front door
    def apply(self, change: DocumentChange) -> ChurnResult:
        started = time.perf_counter()
        if is_noop(self.store, change):
            metrics.DOCUMENT_OPS.labels(op=change.op, result="noop").inc()
            result = ChurnResult(doc_id=change.doc_id, op="noop", status="unchanged")
            result.sync_wall_ms = (time.perf_counter() - started) * 1000
            return result

        # Deletion is pure graph work and completes here; add/update persist intent and
        # enqueue the model calls.
        result = self._delete(change) if change.op == "delete" else self._add_or_update(change)

        result.sync_wall_ms = (time.perf_counter() - started) * 1000
        metrics.UPDATE_LATENCY.labels(op=result.op).observe(result.sync_wall_ms)
        metrics.DOCUMENT_OPS.labels(op=result.op, result="ok").inc()
        return result

    # ------------------------------------------------------------------ add / update
    def _add_or_update(self, change: DocumentChange) -> ChurnResult:
        if change.content is None:
            raise ValueError(f"{change.op} of {change.doc_id} requires content")

        previous = self.store.get_document(change.doc_id)
        op = "update" if previous is not None and previous.status == "active" else "add"
        # What was already stale before this write, so the receipt can report what *this*
        # change dirtied rather than everything currently outstanding.
        before_dirty = {c.community_id for c in self.store.dirty_communities()}
        doc = upsert_document_record(self.store, change)

        old_chunks = self.store.chunks_for_document(doc.doc_id) if op == "update" else []
        new_chunks = chunk_document(
            doc.doc_id, change.content, self.settings.chunk_size, self.settings.chunk_overlap
        )
        diff = diff_chunks(old_chunks, new_chunks)

        # Chunks that survive keep their id, their embedding and every relation that cites
        # them; only their position is refreshed.
        for chunk in diff.moved:
            stored = self.store.get_chunk(chunk.chunk_id)
            if stored is not None:
                stored.position = chunk.position
                self.store.upsert_chunk(stored)

        mutations: list[GraphMutation] = []
        gc_summary = None
        if diff.removed:
            gc_summary, gc_mutations = gc.collect(self.store, set(diff.removed))
            mutations.extend(gc_mutations)

        for chunk in diff.added:
            self.store.upsert_chunk(chunk)

        self.dirty.mark_from_mutations(mutations)
        jobs, inline_mutations = self._enqueue_ingest(doc.doc_id, [c.chunk_id for c in diff.added])
        # When extraction ran inline (no worker configured) its mutations belong in the
        # receipt: the graph really did change, and a receipt reporting nothing would be
        # a lie about what the write did. With a real queue this stays empty, because at
        # that point the extraction genuinely has not happened yet.
        mutations.extend(inline_mutations)
        # New entities have no community when their mutation is emitted - placement runs
        # afterwards - so the dirty set is read from the store rather than from the
        # mutations, minus whatever was already stale on the way in.
        dirty = sorted({c.community_id for c in self.store.dirty_communities()} - before_dirty)

        log.info("%s %s: %s", op, doc.doc_id, diff.summary())
        return ChurnResult(
            doc_id=doc.doc_id,
            op=op,
            status="queued" if jobs else "applied",
            mutations=mutations,
            dirty_communities=dirty,
            gc_summary=gc_summary,
            chunks_added=len(diff.added),
            chunks_reused=len(diff.kept),
            chunks_removed=len(diff.removed),
            jobs_enqueued=jobs,
        )

    # ------------------------------------------------------------------ delete
    def _delete(self, change: DocumentChange) -> ChurnResult:
        doc = self.store.get_document(change.doc_id)
        if doc is None:
            return ChurnResult(doc_id=change.doc_id, op="noop", status="not_found")

        chunk_ids = {chunk.chunk_id for chunk in self.store.chunks_for_document(doc.doc_id)}
        summary, mutations = gc.collect(self.store, chunk_ids)

        doc.status = "deleted"
        doc.text = ""
        doc.updated_at = utcnow()
        self.store.upsert_document(doc)

        dirty = self.dirty.mark_from_mutations(mutations)
        jobs = self._enqueue_recompute()
        return ChurnResult(
            doc_id=doc.doc_id,
            op="delete",
            status="applied",
            mutations=mutations,
            dirty_communities=dirty,
            gc_summary=summary,
            chunks_removed=summary.chunks_removed,
            jobs_enqueued=jobs,
        )

    # ------------------------------------------------------------------ slow path
    def ingest_chunks(self, doc_id: str, chunk_ids: list[str]) -> list[GraphMutation]:
        """Embed, extract and graft the new chunks. Called by a worker, never by a handler."""
        chunks: list[Chunk] = [
            chunk for chunk in (self.store.get_chunk(cid) for cid in chunk_ids) if chunk is not None
        ]
        if not chunks:
            return []

        missing = [chunk for chunk in chunks if not chunk.embedding]
        if missing:
            for chunk, embedding in zip(
                missing, self.embedder.embed_batch([c.text for c in missing]), strict=True
            ):
                chunk.embedding = embedding
                self.store.upsert_chunk(chunk)

        mutations: list[GraphMutation] = []
        new_entities: list[str] = []
        for chunk in chunks:
            extraction = self.extractor.extract_chunk(chunk)
            if not extraction.entities:
                continue
            embeddings = self.embedder.embed_batch(
                [f"{e.name}. {e.description}" for e in extraction.entities]
            )
            resolved: dict[str, str] = {}  # extracted name (lower) -> entity id
            for extracted, embedding in zip(extraction.entities, embeddings, strict=True):
                candidate = self.resolver.candidate_entity(
                    extracted.name, extracted.type, extracted.description, embedding
                )
                entity_id, decision = self.resolver.resolve(candidate, chunk.chunk_id)
                if decision == "merge" and entity_id != candidate.entity_id:
                    # Merged under a different canonical name: keep this surface form so
                    # retrieval and future resolution can still find the entity by it.
                    candidate.aliases.add(extracted.name)
                candidate.entity_id = entity_id
                mutation = ops.add_entity(self.store, candidate, chunk.chunk_id)
                mutations.append(mutation)
                if mutation.kind == "add_entity":
                    new_entities.append(entity_id)
                resolved[extracted.name.lower()] = entity_id

            for relation in extraction.relations:
                source = resolved.get(relation.source.lower())
                target = resolved.get(relation.target.lower())
                if not source or not target or source == target:
                    continue
                mutations.append(
                    ops.add_relation(
                        self.store,
                        source,
                        target,
                        relation.relation_type,
                        chunk.chunk_id,
                        relation.description,
                        relation.weight,
                    )
                )

        # Placement runs after every edge for this batch exists, so the neighbour vote sees
        # the real neighbourhood rather than a half-built one.
        if new_entities:
            place_all(self.store, new_entities, self.settings.max_community_size)

        touched = {
            entity.community_id
            for entity in (self.store.get_entity(m.payload.get("entity_id", "")) for m in mutations)
            if entity is not None and entity.community_id
        }
        for mutation in mutations:
            for community_id in mutation.affected_communities:
                touched.add(community_id)
        self.dirty.mark(sorted(touched), reason="ingest")
        self._refresh_index_gauges()
        log.info("ingested %d chunks of %s -> %d mutations", len(chunks), doc_id, len(mutations))
        return mutations

    # ------------------------------------------------------------------ helpers
    def _enqueue_ingest(
        self, doc_id: str, chunk_ids: list[str]
    ) -> tuple[list[str], list[GraphMutation]]:
        """Returns ``(job_ids, mutations_applied_inline)``.

        Exactly one of the two is ever non-empty: a configured queue hands back job ids and
        does the work later, while an unconfigured one does the work now and hands back
        what it changed.
        """
        if not chunk_ids:
            return [], []
        # A queue that does not actually defer is indistinguishable from no queue as far as
        # the receipt is concerned - the work is done by the time this returns either way,
        # so run it directly and report what it changed.
        if self.queue is None or getattr(self.queue, "synchronous", False):
            return [], self.ingest_chunks(doc_id, chunk_ids)
        return [self.queue.enqueue("ingest_chunks", doc_id=doc_id, chunk_ids=chunk_ids)], []

    def _enqueue_recompute(self) -> list[str]:
        if self.queue is None:
            return []
        return [self.queue.enqueue("recompute_dirty")]

    def _refresh_index_gauges(self) -> None:
        metrics.observe_index(self.store.stats())

    def chunk_diff_for(self, doc_id: str, content: str) -> ChunkDiff:
        """Dry-run a document update. Used by the eval harness to price an edit before
        applying it, and handy for debugging."""
        old = self.store.chunks_for_document(doc_id)
        new = chunk_document(doc_id, content, self.settings.chunk_size, self.settings.chunk_overlap)
        return diff_chunks(old, new)
