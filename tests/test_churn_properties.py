"""Property-based tests for the churn engine.

Example-based tests show the engine works on the corpus we happened to write. These check
the invariants hold for *arbitrary* interleavings of add / update / delete, which is what
"correct under churn" actually means. Four properties:

P1. Provenance closure - nothing exists without a live chunk supporting it.
P2. Deletion minimality - deleting a document removes exactly what it uniquely supported.
P3. Idempotence - replaying a change never changes the graph a second time.
P4. Order independence - the final graph depends on the surviving document set, not on the
    order the churn arrived in.
"""

from __future__ import annotations

from conftest import make_settings
from hypothesis import HealthCheck, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from egraph.pipeline import build_pipeline
from egraph.schemas import DocumentChange
from fixtures.mini_corpus import BASE_DOCS, NEW_DOCS, UPDATED_DOCS

ALL_DOCS = {**BASE_DOCS, **NEW_DOCS}
DOC_IDS = sorted(ALL_DOCS)

SLOW = hyp_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


def fresh_pipeline():
    pipe = build_pipeline(make_settings())
    pipe.reset()
    return pipe


def apply_ops(pipeline, operations: list[tuple[str, str]]) -> None:
    for op, doc_id in operations:
        if op == "add":
            pipeline.apply(
                DocumentChange(op="add", doc_id=doc_id, uri=doc_id, content=ALL_DOCS[doc_id])
            )
        elif op == "update":
            content = UPDATED_DOCS.get(doc_id, ALL_DOCS[doc_id] + "\n\nAn appended paragraph.")
            pipeline.apply(DocumentChange(op="update", doc_id=doc_id, content=content))
        else:
            pipeline.apply(DocumentChange(op="delete", doc_id=doc_id))


def assert_closed(pipeline) -> None:
    """P1: every entity and relation traces to a chunk that still exists."""
    live = {chunk.chunk_id for chunk in pipeline.store.all_chunks()}
    for entity in pipeline.store.all_entities():
        assert entity.mentions, f"{entity.canonical_name} has no provenance"
        assert entity.mentions <= live, f"{entity.canonical_name} cites a dead chunk"
    for relation in pipeline.store.all_relations():
        assert relation.provenance, f"{relation.relation_id} has no provenance"
        assert relation.provenance <= live, f"{relation.relation_id} cites a dead chunk"
        assert pipeline.store.get_entity(relation.source_id) is not None
        assert pipeline.store.get_entity(relation.target_id) is not None


operations = st.lists(
    st.tuples(st.sampled_from(["add", "update", "delete"]), st.sampled_from(DOC_IDS)),
    min_size=1,
    max_size=12,
)


@given(ops=operations)
@SLOW
def test_p1_provenance_closure_under_arbitrary_churn(ops):
    pipeline = fresh_pipeline()
    try:
        apply_ops(pipeline, ops)
        assert_closed(pipeline)
        pipeline.drain()
        assert_closed(pipeline)
    finally:
        pipeline.close()


@given(
    seed_docs=st.lists(st.sampled_from(DOC_IDS), min_size=2, max_size=6, unique=True),
    data=st.data(),
)
@SLOW
def test_p2_deletion_removes_exactly_the_uniquely_supported_subgraph(seed_docs, data):
    pipeline = fresh_pipeline()
    try:
        apply_ops(pipeline, [("add", doc_id) for doc_id in seed_docs])
        victim = data.draw(st.sampled_from(seed_docs))
        victim_chunks = {c.chunk_id for c in pipeline.store.chunks_for_document(victim)}

        before_entities = {e.entity_id: set(e.mentions) for e in pipeline.store.all_entities()}
        before_relations = {
            r.relation_id: set(r.provenance) for r in pipeline.store.all_relations()
        }

        pipeline.apply(DocumentChange(op="delete", doc_id=victim))

        after_entities = {e.entity_id: set(e.mentions) for e in pipeline.store.all_entities()}
        after_relations = {r.relation_id: set(r.provenance) for r in pipeline.store.all_relations()}

        expected_entities = {
            eid: mentions - victim_chunks
            for eid, mentions in before_entities.items()
            if mentions - victim_chunks
        }
        expected_relations = {
            rid: provenance - victim_chunks
            for rid, provenance in before_relations.items()
            if provenance - victim_chunks
        }
        assert after_entities == expected_entities
        assert after_relations == expected_relations
    finally:
        pipeline.close()


@given(ops=operations)
@SLOW
def test_p3_replaying_a_change_is_a_noop(ops):
    pipeline = fresh_pipeline()
    try:
        apply_ops(pipeline, ops)
        pipeline.drain()
        snapshot = _snapshot(pipeline)
        apply_ops(pipeline, ops[-1:])  # replay the last change verbatim
        assert _snapshot(pipeline) == snapshot
    finally:
        pipeline.close()


@given(docs=st.lists(st.sampled_from(DOC_IDS), min_size=2, max_size=5, unique=True))
@SLOW
def test_p4_final_graph_is_independent_of_arrival_order(docs):
    forward = fresh_pipeline()
    backward = fresh_pipeline()
    try:
        apply_ops(forward, [("add", doc_id) for doc_id in docs])
        apply_ops(backward, [("add", doc_id) for doc_id in reversed(docs)])
        # Community *ids* depend on insertion order (they are seeded from the first member),
        # so compare the graph itself: entities, relations and their provenance.
        assert _graph_only(forward) == _graph_only(backward)
    finally:
        forward.close()
        backward.close()


def _graph_only(pipeline) -> tuple:
    entities = sorted(
        (e.canonical_name, e.type, tuple(sorted(e.mentions))) for e in pipeline.store.all_entities()
    )
    relations = sorted(
        (r.source_id, r.target_id, r.relation_type, tuple(sorted(r.provenance)))
        for r in pipeline.store.all_relations()
    )
    return entities, relations


def _snapshot(pipeline) -> tuple:
    return (
        *_graph_only(pipeline),
        sorted(
            (c.community_id, tuple(sorted(c.members)), c.dirty)
            for c in pipeline.store.all_communities()
        ),
    )
