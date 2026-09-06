"""The five mutations, individually."""

from __future__ import annotations

import pytest
from conftest import ingest

from egraph.churn import mutations as ops
from egraph.schemas import Entity, entity_key, relation_key
from fixtures.mini_corpus import BASE_DOCS


def _entity(name: str, etype: str = "concept") -> Entity:
    return Entity(entity_id=entity_key(name, etype), canonical_name=name, type=etype)


def test_add_entity_creates_with_provenance(pipeline):
    mutation = ops.add_entity(pipeline.store, _entity("Aurora Engine", "technology"), "chunk-1")
    assert mutation.kind == "add_entity"
    assert mutation.provenance == ["chunk-1"]
    stored = pipeline.store.get_entity(mutation.payload["entity_id"])
    assert stored is not None and stored.mentions == {"chunk-1"}


def test_add_entity_twice_is_an_update_not_a_duplicate(pipeline):
    ops.add_entity(pipeline.store, _entity("Aurora Engine", "technology"), "chunk-1")
    second = ops.add_entity(pipeline.store, _entity("Aurora Engine", "technology"), "chunk-2")
    assert second.kind == "update_entity"
    assert len(pipeline.store.all_entities()) == 1
    stored = pipeline.store.all_entities()[0]
    assert stored.mentions == {"chunk-1", "chunk-2"}


def test_add_relation_unions_provenance(pipeline):
    a, b = _entity("A"), _entity("B")
    ops.add_entity(pipeline.store, a, "chunk-1")
    ops.add_entity(pipeline.store, b, "chunk-1")
    ops.add_relation(pipeline.store, a.entity_id, b.entity_id, "uses", "chunk-1")
    ops.add_relation(pipeline.store, a.entity_id, b.entity_id, "uses", "chunk-2")

    relation = pipeline.store.get_relation(relation_key(a.entity_id, b.entity_id, "uses"))
    assert relation is not None
    assert relation.provenance == {"chunk-1", "chunk-2"}
    assert len(pipeline.store.all_relations()) == 1


def test_expire_relation_weakens_before_it_deletes(pipeline):
    a, b = _entity("A"), _entity("B")
    ops.add_entity(pipeline.store, a, "chunk-1")
    ops.add_entity(pipeline.store, b, "chunk-1")
    ops.add_relation(pipeline.store, a.entity_id, b.entity_id, "uses", "chunk-1")
    ops.add_relation(pipeline.store, a.entity_id, b.entity_id, "uses", "chunk-2")
    relation_id = relation_key(a.entity_id, b.entity_id, "uses")

    weakened = ops.expire_relation(pipeline.store, relation_id, {"chunk-1"})
    assert weakened is not None and weakened.payload["expired"] is False
    assert pipeline.store.get_relation(relation_id).provenance == {"chunk-2"}

    expired = ops.expire_relation(pipeline.store, relation_id, {"chunk-2"})
    assert expired is not None and expired.payload["expired"] is True
    assert pipeline.store.get_relation(relation_id) is None


def test_expire_relation_with_unrelated_chunks_is_a_noop(pipeline):
    a, b = _entity("A"), _entity("B")
    ops.add_entity(pipeline.store, a, "chunk-1")
    ops.add_entity(pipeline.store, b, "chunk-1")
    ops.add_relation(pipeline.store, a.entity_id, b.entity_id, "uses", "chunk-1")
    relation_id = relation_key(a.entity_id, b.entity_id, "uses")
    assert ops.expire_relation(pipeline.store, relation_id, {"chunk-99"}) is None
    assert pipeline.store.get_relation(relation_id) is not None


def test_remove_entity_refuses_to_lose_provenance(pipeline):
    entity = _entity("Aurora Engine", "technology")
    ops.add_entity(pipeline.store, entity, "chunk-1")
    with pytest.raises(ValueError, match="still supported"):
        ops.remove_entity(pipeline.store, entity.entity_id)
    assert pipeline.store.get_entity(entity.entity_id) is not None


def test_remove_entity_takes_its_relations_with_it(pipeline):
    a, b = _entity("A"), _entity("B")
    ops.add_entity(pipeline.store, a, "chunk-1")
    ops.add_entity(pipeline.store, b, "chunk-1")
    ops.add_relation(pipeline.store, a.entity_id, b.entity_id, "uses", "chunk-1")

    stored = pipeline.store.get_entity(a.entity_id)
    stored.mentions.clear()
    pipeline.store.upsert_entity(stored)
    ops.remove_entity(pipeline.store, a.entity_id)

    assert pipeline.store.get_entity(a.entity_id) is None
    assert pipeline.store.all_relations() == []


def test_update_entity_records_affected_communities(loaded):
    entity = max(loaded.store.all_entities(), key=lambda e: e.degree)
    mutation = ops.update_entity(loaded.store, entity.entity_id, description="revised")
    assert mutation is not None
    assert mutation.kind == "update_entity"
    assert mutation.affected_communities == [entity.community_id]
    assert loaded.store.get_entity(entity.entity_id).description == "revised"


def test_mutations_are_emitted_for_a_real_ingest(pipeline):
    ingest(pipeline, {"helios-overview": BASE_DOCS["helios-overview"]})
    kinds = {m.kind for m in pipeline.engine.ingest_chunks("helios-overview", [])}
    assert kinds == set()  # already ingested inline; nothing left to do
    assert pipeline.store.all_entities()
    assert pipeline.store.all_relations()
