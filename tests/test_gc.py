"""Garbage collection: orphans, dangling edges, empty communities."""

from __future__ import annotations

from conftest import ingest

from egraph.churn import gc
from egraph.churn import mutations as ops
from egraph.schemas import DocumentChange, Entity, entity_key
from fixtures.mini_corpus import BASE_DOCS


def _entity(name: str) -> Entity:
    return Entity(entity_id=entity_key(name, "concept"), canonical_name=name)


def test_collect_on_an_empty_set_changes_nothing(loaded):
    before = len(loaded.store.all_entities()), len(loaded.store.all_relations())
    summary, mutations = gc.collect(loaded.store, set())
    assert mutations == []
    assert summary.entities_removed == 0
    assert (len(loaded.store.all_entities()), len(loaded.store.all_relations())) == before


def test_collect_reports_what_it_did(loaded):
    chunk_ids = {c.chunk_id for c in loaded.store.chunks_for_document("kestrel-systems")}
    summary, mutations = gc.collect(loaded.store, chunk_ids)

    assert summary.chunks_removed == len(chunk_ids)
    assert summary.relations_expired == len(summary.expired_relation_ids)
    assert summary.entities_removed == len(summary.removed_entity_ids)
    assert len(mutations) == (
        summary.relations_expired + summary.relations_weakened + summary.entities_removed
    )
    for entity_id in summary.removed_entity_ids:
        assert loaded.store.get_entity(entity_id) is None
    for relation_id in summary.expired_relation_ids:
        assert loaded.store.get_relation(relation_id) is None


def test_an_entity_with_surviving_support_is_never_collected(loaded):
    # Kestrel Systems is mentioned by both its own document and fjord-protocol.
    kestrel = next(e for e in loaded.store.all_entities() if e.canonical_name == "Kestrel Systems")
    assert len({loaded.store.get_chunk(c).doc_id for c in kestrel.mentions}) > 1

    loaded.apply(DocumentChange(op="delete", doc_id="kestrel-systems"))
    survivor = loaded.store.get_entity(kestrel.entity_id)
    assert survivor is not None, "an entity supported by another document must survive"
    assert survivor.mentions, "the survivor must keep its remaining provenance"


def test_sweep_orphans_is_a_noop_on_a_healthy_index(loaded):
    assert gc.sweep_orphans(loaded.store) == []


def test_sweep_orphans_cleans_up_after_a_partial_failure(pipeline):
    """Simulates a worker dying between detaching mentions and collecting the entity."""
    entity = _entity("Ghost")
    ops.add_entity(pipeline.store, entity, "chunk-1")
    stored = pipeline.store.get_entity(entity.entity_id)
    stored.mentions.clear()
    pipeline.store.upsert_entity(stored)

    collected = gc.sweep_orphans(pipeline.store)
    assert [m.payload["entity_id"] for m in collected] == [entity.entity_id]
    assert pipeline.store.get_entity(entity.entity_id) is None


def test_no_dangling_relations_after_deletion(loaded):
    loaded.apply(DocumentChange(op="delete", doc_id="northwind"))
    loaded.apply(DocumentChange(op="delete", doc_id="kestrel-systems"))
    assert gc.dangling_relations(loaded.store) == []


def test_emptied_communities_are_removed(pipeline):
    ingest(pipeline, BASE_DOCS)
    pipeline.drain()
    before = len(pipeline.store.all_communities())

    for doc_id in BASE_DOCS:
        pipeline.apply(DocumentChange(op="delete", doc_id=doc_id))

    assert pipeline.store.all_entities() == []
    assert pipeline.store.all_relations() == []
    assert pipeline.store.all_communities() == [], "communities outlived every member"
    assert before > 0


def test_deleting_everything_leaves_a_queryable_empty_index(pipeline):
    from egraph.schemas import QueryRequest

    ingest(pipeline, BASE_DOCS)
    pipeline.drain()
    for doc_id in BASE_DOCS:
        pipeline.apply(DocumentChange(op="delete", doc_id=doc_id))
    pipeline.drain()

    answer = pipeline.query(QueryRequest(question="Who founded Helios Labs?"))
    assert "do not contain" in answer.text
    assert answer.citations == []
