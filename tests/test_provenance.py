"""Provenance is the invariant the whole system rests on.

If any of these fail, deletion is unsound and the maintenance claim is void.
"""

from __future__ import annotations

from conftest import graph_snapshot, ingest

from egraph.schemas import DocumentChange, QueryRequest
from fixtures.mini_corpus import BASE_DOCS


def test_every_relation_has_provenance(loaded):
    for relation in loaded.store.all_relations():
        assert relation.provenance, f"{relation.relation_id} exists with no supporting chunk"


def test_every_entity_has_a_mention(loaded):
    for entity in loaded.store.all_entities():
        assert entity.mentions, f"{entity.canonical_name} exists with no supporting chunk"


def test_provenance_points_at_live_chunks(loaded):
    live = {chunk.chunk_id for chunk in loaded.store.all_chunks()}
    for relation in loaded.store.all_relations():
        assert relation.provenance <= live, "relation cites a chunk that no longer exists"
    for entity in loaded.store.all_entities():
        assert entity.mentions <= live, "entity cites a chunk that no longer exists"


def test_answers_cite_real_documents(loaded):
    answer = loaded.query(QueryRequest(question="Who founded Helios Labs?"))
    assert answer.citations, "an answer with no citation has no provenance"
    known = {d.doc_id for d in loaded.store.list_documents()}
    for citation in answer.citations:
        assert citation in known, f"citation {citation} is not a document in the index"


def test_delete_removes_only_uniquely_supported_state(pipeline):
    """The central claim: deletion removes exactly what the document uniquely supported."""
    ingest(pipeline, BASE_DOCS)
    pipeline.drain()

    victim = "kestrel-systems"
    victim_chunks = {c.chunk_id for c in pipeline.store.chunks_for_document(victim)}
    before = graph_snapshot(pipeline)

    # What *should* survive: anything with support outside the victim's chunks.
    expected_surviving_relations = {
        rid: value for rid, value in before["relations"].items() if set(value[3]) - victim_chunks
    }
    expected_dead_relations = set(before["relations"]) - set(expected_surviving_relations)
    expected_surviving_entities = {
        eid for eid, value in before["entities"].items() if set(value[1]) - victim_chunks
    }
    expected_dead_entities = set(before["entities"]) - expected_surviving_entities

    pipeline.apply(DocumentChange(op="delete", doc_id=victim))
    after = graph_snapshot(pipeline)

    assert set(after["relations"]) == set(
        expected_surviving_relations
    ), "deletion removed the wrong set of relations"
    assert (
        set(after["entities"]) == expected_surviving_entities
    ), "deletion removed the wrong set of entities"
    assert expected_dead_relations, "test corpus must contain uniquely-supported relations"
    assert expected_dead_entities, "test corpus must contain uniquely-supported entities"

    # Survivors keep exactly their non-victim provenance - no more, no less.
    for rid, (_, _, _, provenance) in after["relations"].items():
        assert set(provenance) == set(before["relations"][rid][3]) - victim_chunks
    for eid, (_, mentions) in after["entities"].items():
        assert set(mentions) == set(before["entities"][eid][1]) - victim_chunks


def test_delete_leaves_unrelated_documents_untouched(pipeline):
    ingest(pipeline, BASE_DOCS)
    pipeline.drain()

    unrelated = "helios-overview"
    unrelated_chunks = {c.chunk_id for c in pipeline.store.chunks_for_document(unrelated)}
    before = graph_snapshot(pipeline)

    pipeline.apply(DocumentChange(op="delete", doc_id="northwind"))
    after = graph_snapshot(pipeline)

    # Every chunk of the unrelated document is still there, byte for byte.
    assert unrelated_chunks <= after["chunks"]
    # Entities supported *only* by the unrelated document are entirely unchanged.
    for eid, (name, mentions) in before["entities"].items():
        if set(mentions) <= unrelated_chunks:
            assert (
                eid in after["entities"]
            ), f"{name} was collected but only {unrelated} supports it"
            assert after["entities"][eid] == (name, mentions)


def test_delete_is_idempotent(loaded):
    first = loaded.apply(DocumentChange(op="delete", doc_id="northwind"))
    snapshot = graph_snapshot(loaded)
    second = loaded.apply(DocumentChange(op="delete", doc_id="northwind"))
    assert first.op == "delete"
    assert second.op == "noop"
    assert graph_snapshot(loaded) == snapshot


def test_no_orphans_or_unsupported_state_after_churn(loaded):
    loaded.apply(DocumentChange(op="delete", doc_id="kestrel-systems"))
    loaded.apply(DocumentChange(op="delete", doc_id="northwind"))
    loaded.drain()
    assert [e.entity_id for e in loaded.store.all_entities() if not e.mentions] == []
    assert [r.relation_id for r in loaded.store.all_relations() if not r.provenance] == []
    live = {c.chunk_id for c in loaded.store.all_chunks()}
    for relation in loaded.store.all_relations():
        assert relation.provenance & live, "relation survives citing only dead chunks"
