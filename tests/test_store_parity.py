"""Every store backend must behave exactly like the in-memory one.

The memory backend is the semantics oracle: it is what the tests, the eval harness and the
offline demo run against, so every claim in the report is a claim about *its* behaviour. If
the deployed store diverges, the report describes a system nobody is running.

Each test runs once per available backend. A backend whose server is not up is skipped, so
the suite stays runnable with no daemon at all. To run the others:

    docker compose up -d arcadedb postgres
    EGRAPH_ARCADEDB_URL=http://localhost:2480     EGRAPH_TEST_POSTGRES_DSN=postgresql://egraph:egraph@localhost:5432/egraph         pytest tests/test_store_parity.py -v

They use throwaway databases and wipe them between tests, so they never touch a real index.

This suite has earned its keep. Running it against a live ArcadeDB for the first time found
four bugs that no amount of reading the code would have surfaced - a DDL comment containing
a semicolon, `DELETE VERTEX` needing a `FROM`, `LIST OF FLOAT` rejecting Python floats, and
`ORDER BY` with an implicit projection returning one phantom row per storage bucket. The
last would have silently corrupted the recompute queue.
"""

from __future__ import annotations

import os

import pytest

from egraph.churn import gc
from egraph.churn import mutations as ops
from egraph.gateway.embeddings import hash_embed
from egraph.schemas import Chunk, Community, Entity, SourceDocument, entity_key, relation_key
from egraph.store.base import GraphStore
from egraph.store.memory import MemoryStore

ARCADE_URL = os.getenv("EGRAPH_ARCADEDB_URL", "http://localhost:2480")
POSTGRES_DSN = os.getenv("EGRAPH_TEST_POSTGRES_DSN", "")
DIM = 64


def _arcadedb_store():
    """Build an ArcadeDB store, or return None if the server is not up."""
    try:
        from egraph.store.arcadedb import ArcadeDBStore
        from egraph.store.client import ArcadeDBClient
    except ImportError:  # pragma: no cover
        return None

    client = ArcadeDBClient(
        url=ARCADE_URL,
        database=os.getenv("EGRAPH_PARITY_DATABASE", "egraph_parity"),
        user=os.getenv("EGRAPH_ARCADEDB_USER", "root"),
        password=os.getenv("EGRAPH_ARCADEDB_PASSWORD", "playwithdata"),
        timeout=10.0,
        retries=1,
    )
    if not client.ping():
        return None
    store = ArcadeDBStore(client, dim=DIM)
    store.migrate()
    return store


def _postgres_store():
    """Build a Postgres store, or return None if no DSN is configured or it is unreachable."""
    if not POSTGRES_DSN:
        return None
    try:
        from egraph.store.postgres import PostgresStore

        store = PostgresStore(POSTGRES_DSN, dim=DIM, max_size=2)
    except Exception:  # noqa: BLE001 - an unavailable backend is a skip, not an error
        return None
    if not store.ping():
        return None
    store.migrate()
    return store


ARCADE = _arcadedb_store()
POSTGRES = _postgres_store()

needs_arcadedb = pytest.mark.skipif(
    ARCADE is None, reason=f"ArcadeDB not reachable at {ARCADE_URL}"
)
needs_postgres = pytest.mark.skipif(
    POSTGRES is None, reason="set EGRAPH_TEST_POSTGRES_DSN to run the Postgres parity tests"
)


@pytest.fixture(params=["memory", "arcadedb", "postgres"])
def store(request) -> GraphStore:
    """Every test in this module runs once per backend."""
    if request.param == "memory":
        backend: GraphStore = MemoryStore(dim=DIM)
    elif request.param == "arcadedb":
        if ARCADE is None:
            pytest.skip(f"ArcadeDB not reachable at {ARCADE_URL}")
        backend = ARCADE
    else:
        if POSTGRES is None:
            pytest.skip("set EGRAPH_TEST_POSTGRES_DSN to run the Postgres parity tests")
        backend = POSTGRES
    backend.clear()
    yield backend
    backend.clear()


# ---------------------------------------------------------------------------- helpers
def seed(store: GraphStore) -> dict[str, str]:
    """A tiny graph with the shape the churn tests care about: one relation supported by
    two chunks, one supported by a single chunk."""
    store.upsert_document(
        SourceDocument(doc_id="doc-a", uri="a.md", content_hash="hash-a", text="a")
    )
    store.upsert_document(
        SourceDocument(doc_id="doc-b", uri="b.md", content_hash="hash-b", text="b")
    )
    for doc_id, chunk_id, text in (
        ("doc-a", "chunk-a1", "Helios Labs develops Aurora Engine."),
        ("doc-a", "chunk-a2", "Aurora Engine uses the Fjord Protocol."),
        ("doc-b", "chunk-b1", "Aurora Engine uses the Fjord Protocol."),
    ):
        store.upsert_chunk(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                text=text,
                position=0,
                content_hash=chunk_id,
                embedding=hash_embed(text, DIM),
            )
        )

    ids = {}
    for name, etype, chunks in (
        ("Helios Labs", "organization", ["chunk-a1"]),
        ("Aurora Engine", "technology", ["chunk-a1", "chunk-a2", "chunk-b1"]),
        ("Fjord Protocol", "technology", ["chunk-a2", "chunk-b1"]),
    ):
        entity = Entity(
            entity_id=entity_key(name, etype),
            canonical_name=name,
            type=etype,
            description=f"{name} description",
            embedding=hash_embed(name, DIM),
        )
        for chunk_id in chunks:
            ops.add_entity(store, entity, chunk_id)
        ids[name] = entity.entity_id

    ops.add_relation(store, ids["Helios Labs"], ids["Aurora Engine"], "develops", "chunk-a1")
    # Supported by two chunks in two different documents: must survive one being deleted.
    ops.add_relation(store, ids["Aurora Engine"], ids["Fjord Protocol"], "uses", "chunk-a2")
    ops.add_relation(store, ids["Aurora Engine"], ids["Fjord Protocol"], "uses", "chunk-b1")

    store.upsert_community(
        Community(
            community_id="c-1",
            title="Helios",
            summary="A summary.",
            dirty=False,
            members=set(ids.values()),
        )
    )
    for entity_id in ids.values():
        entity = store.get_entity(entity_id)
        entity.community_id = "c-1"
        store.upsert_entity(entity)
    return ids


# ---------------------------------------------------------------------------- tests
def test_documents_round_trip(store: GraphStore):
    seed(store)
    doc = store.get_document("doc-a")
    assert doc is not None and doc.uri == "a.md" and doc.status == "active"
    assert {d.doc_id for d in store.list_documents()} == {"doc-a", "doc-b"}
    assert {d.doc_id for d in store.list_documents(status="active")} == {"doc-a", "doc-b"}
    assert store.find_document_by_hash("hash-a").doc_id == "doc-a"
    assert store.find_document_by_hash("nope") is None


def test_chunks_round_trip(store: GraphStore):
    seed(store)
    assert {c.chunk_id for c in store.chunks_for_document("doc-a")} == {"chunk-a1", "chunk-a2"}
    chunk = store.get_chunk("chunk-a1")
    assert chunk is not None and chunk.embedding and len(chunk.embedding) == DIM
    assert len(store.all_chunks()) == 3


def test_entities_and_mentions_round_trip(store: GraphStore):
    ids = seed(store)
    aurora = store.get_entity(ids["Aurora Engine"])
    assert aurora is not None
    assert aurora.mentions == {"chunk-a1", "chunk-a2", "chunk-b1"}
    assert aurora.community_id == "c-1"

    mentioning = {e.entity_id for e in store.entities_mentioning("chunk-b1")}
    assert mentioning == {ids["Aurora Engine"], ids["Fjord Protocol"]}


def test_relations_and_provenance_round_trip(store: GraphStore):
    ids = seed(store)
    relation_id = relation_key(ids["Aurora Engine"], ids["Fjord Protocol"], "uses")
    relation = store.get_relation(relation_id)
    assert relation is not None
    assert relation.provenance == {"chunk-a2", "chunk-b1"}, "provenance must survive the round trip"

    found = {r.relation_id for r in store.relations_with_provenance({"chunk-b1"})}
    assert relation_id in found
    assert len(store.relations_for_entity(ids["Aurora Engine"])) == 2


def test_vector_search_ranks_the_right_entity(store: GraphStore):
    ids = seed(store)
    hits = store.search_entities(hash_embed("Fjord Protocol", DIM), 3)
    assert hits, "vector search returned nothing"
    assert hits[0][0].entity_id == ids["Fjord Protocol"]
    assert hits[0][1] > 0.5


def test_communities_and_dirty_scan_round_trip(store: GraphStore):
    seed(store)
    from egraph.store import communities as community_ops

    assert store.dirty_communities() == []
    community_ops.mark_dirty(store, ["c-1"], reason="test")
    dirty = store.dirty_communities()
    assert [c.community_id for c in dirty] == ["c-1"]
    assert dirty[0].dirty_since is not None

    community_ops.clear_dirty(store, "c-1")
    assert store.dirty_communities() == []
    assert store.get_community("c-1").summarized_members == store.get_community("c-1").members


def test_deletion_semantics_are_identical(store: GraphStore):
    """The behaviour the whole project rests on, checked on both backends."""
    ids = seed(store)
    doc_a_chunks = {"chunk-a1", "chunk-a2"}

    summary, _ = gc.collect(store, doc_a_chunks)

    # Helios Labs was only ever mentioned by doc-a: collected.
    assert store.get_entity(ids["Helios Labs"]) is None
    # Aurora Engine and Fjord Protocol are still mentioned by chunk-b1: they survive.
    aurora = store.get_entity(ids["Aurora Engine"])
    fjord = store.get_entity(ids["Fjord Protocol"])
    assert aurora is not None and aurora.mentions == {"chunk-b1"}
    assert fjord is not None and fjord.mentions == {"chunk-b1"}

    # develops was uniquely supported by chunk-a1: expired.
    assert (
        store.get_relation(relation_key(ids["Helios Labs"], ids["Aurora Engine"], "develops"))
        is None
    )
    # uses was supported by two chunks: survives, weakened to one.
    uses = store.get_relation(relation_key(ids["Aurora Engine"], ids["Fjord Protocol"], "uses"))
    assert uses is not None and uses.provenance == {"chunk-b1"}

    assert summary.entities_removed == 1
    assert summary.relations_expired == 1
    assert summary.relations_weakened == 1
    assert summary.chunks_removed == 2


def test_stats_agree_with_the_contents(store: GraphStore):
    seed(store)
    stats = store.stats()
    assert stats.documents == 2
    assert stats.chunks == 3
    assert stats.entities == 3
    assert stats.relations == 2
    assert stats.communities == 1


def test_clear_empties_everything(store: GraphStore):
    seed(store)
    store.clear()
    assert store.all_entities() == []
    assert store.all_relations() == []
    assert store.all_chunks() == []
    assert store.all_communities() == []
    assert store.list_documents() == []


@needs_arcadedb
def test_arcadedb_schema_migration_is_idempotent():
    """`make up` re-runs the migration on every boot."""
    assert ARCADE is not None
    ARCADE.migrate()
    ARCADE.migrate()
    assert ARCADE.ping()


@needs_arcadedb
def test_arcadedb_audit_trails_round_trip():
    from egraph.schemas import CostRecord, ResolutionDecision

    assert ARCADE is not None
    ARCADE.clear()
    ARCADE.record_cost(CostRecord(operation="extract", tokens_in=10, tokens_out=5, usd=0.001))
    ARCADE.log_resolution(
        ResolutionDecision(
            candidate_name="Helios Labs",
            candidate_type="organization",
            decision="create",
            reason="no candidate cleared both thresholds",
        )
    )
    assert [c.operation for c in ARCADE.list_costs()] == ["extract"]
    assert [d.decision for d in ARCADE.list_resolutions()] == ["create"]
    ARCADE.clear()
