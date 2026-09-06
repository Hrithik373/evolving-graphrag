"""The five graph mutations.

Every write in the system decomposes into these, and each one is responsible for three
things: change the graph, carry the provenance that justified the change, and name the
communities that must be re-summarised because of it. Anything that skips the third is a
silent freshness bug, so the mutation record is what the dirty tracker consumes.
"""

from __future__ import annotations

from egraph.observability import metrics
from egraph.schemas import Entity, GraphMutation, Relation, utcnow
from egraph.store import entities as entity_ops
from egraph.store import relations as relation_ops
from egraph.store.base import GraphStore


def _record(kind: str) -> None:
    metrics.MUTATIONS.labels(kind=kind).inc()


def _communities_of(store: GraphStore, entity_ids: list[str]) -> list[str]:
    found: list[str] = []
    for entity_id in entity_ids:
        entity = store.get_entity(entity_id)
        if entity is not None and entity.community_id and entity.community_id not in found:
            found.append(entity.community_id)
    return found


def add_entity(store: GraphStore, entity: Entity, chunk_id: str) -> GraphMutation:
    """Create an entity (or attach a new mention to an existing one)."""
    existing = store.get_entity(entity.entity_id)
    if existing is None:
        entity.mentions.add(chunk_id)
        if entity.description:
            entity.descriptions[chunk_id] = entity.description
        entity_ops.refresh_description(entity)
        store.upsert_entity(entity)
        kind = "add_entity"
    else:
        entity_ops.attach_mention(store, existing, chunk_id)
        if entity.description:
            existing.descriptions[chunk_id] = entity.description
        changed = entity_ops.refresh_description(existing)
        if entity.aliases - existing.aliases:
            existing.aliases |= entity.aliases
            changed = True
        if changed:
            existing.updated_at = utcnow()
        store.upsert_entity(existing)
        kind = "update_entity"
    _record(kind)
    return GraphMutation(
        kind=kind,
        payload={
            "entity_id": entity.entity_id,
            "canonical_name": entity.canonical_name,
            "type": entity.type,
        },
        provenance=[chunk_id],
        affected_communities=_communities_of(store, [entity.entity_id]),
    )


def update_entity(store: GraphStore, entity_id: str, **fields) -> GraphMutation | None:
    entity = store.get_entity(entity_id)
    if entity is None:
        return None
    for key, value in fields.items():
        if hasattr(entity, key):
            setattr(entity, key, value)
    entity.updated_at = utcnow()
    store.upsert_entity(entity)
    _record("update_entity")
    return GraphMutation(
        kind="update_entity",
        payload={"entity_id": entity_id, **{k: str(v)[:120] for k, v in fields.items()}},
        provenance=sorted(entity.mentions),
        affected_communities=_communities_of(store, [entity_id]),
    )


def remove_entity(
    store: GraphStore, entity_id: str, reason: str = "orphan"
) -> GraphMutation | None:
    """Delete an entity and every relation attached to it.

    Only ever called on an entity with no surviving mentions - see ``churn/gc.py``. An
    entity with provenance is never removed, no matter what a diff says.
    """
    entity = store.get_entity(entity_id)
    if entity is None:
        return None
    if entity.mentions:
        raise ValueError(
            f"refusing to remove entity {entity_id} still supported by "
            f"{len(entity.mentions)} chunk(s) - that would lose provenance"
        )
    affected = _communities_of(store, [entity_id])
    store.remove_entity(entity_id)
    _record("remove_entity")
    return GraphMutation(
        kind="remove_entity",
        payload={"entity_id": entity_id, "canonical_name": entity.canonical_name, "reason": reason},
        provenance=[],
        affected_communities=affected,
    )


def add_relation(
    store: GraphStore,
    source_id: str,
    target_id: str,
    relation_type: str,
    chunk_id: str,
    description: str = "",
    weight: float = 1.0,
) -> GraphMutation:
    relation = relation_ops.upsert_with_provenance(
        store, source_id, target_id, relation_type, chunk_id, description, weight
    )
    entity_ops.refresh_degree(store, source_id)
    entity_ops.refresh_degree(store, target_id)
    _record("add_relation")
    return GraphMutation(
        kind="add_relation",
        payload={
            "relation_id": relation.relation_id,
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": relation_type,
            "support": len(relation.provenance),
        },
        provenance=[chunk_id],
        affected_communities=_communities_of(store, [source_id, target_id]),
    )


def expire_relation(
    store: GraphStore, relation_id: str, chunk_ids: set[str]
) -> GraphMutation | None:
    """Withdraw the support these chunks gave a relation.

    If support remains the relation survives, weakened. If the provenance set empties, the
    relation is deleted - it no longer has a source in the corpus.
    """
    relation: Relation | None = store.get_relation(relation_id)
    if relation is None:
        return None
    remaining = relation.provenance - chunk_ids
    if remaining == relation.provenance:
        return None
    affected = _communities_of(store, [relation.source_id, relation.target_id])
    if remaining:
        relation.provenance = remaining
        # The surviving description must come from a chunk that still exists.
        relation_ops.refresh_description(relation)
        relation.updated_at = utcnow()
        store.upsert_relation(relation)
        kind_payload = {"expired": False, "support": len(remaining)}
    else:
        store.remove_relation(relation_id)
        kind_payload = {"expired": True, "support": 0}
    entity_ops.refresh_degree(store, relation.source_id)
    entity_ops.refresh_degree(store, relation.target_id)
    _record("expire_relation")
    return GraphMutation(
        kind="expire_relation",
        payload={
            "relation_id": relation_id,
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "relation_type": relation.relation_type,
            **kind_payload,
        },
        provenance=sorted(chunk_ids),
        affected_communities=affected,
    )
