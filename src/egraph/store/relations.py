"""Provenance-set relation operations - the retraction lever in code form."""

from __future__ import annotations

from egraph.schemas import Relation, relation_key, utcnow
from egraph.store.base import GraphStore


def upsert_with_provenance(
    store: GraphStore,
    source_id: str,
    target_id: str,
    relation_type: str,
    chunk_id: str,
    description: str = "",
    weight: float = 1.0,
) -> Relation:
    """Add support for a relation from one chunk. Re-asserting the same fact from the same
    chunk is a no-op; asserting it from a new chunk strengthens the edge."""
    relation_id = relation_key(source_id, target_id, relation_type)
    relation = store.get_relation(relation_id)
    if relation is None:
        relation = Relation(
            relation_id=relation_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            description=description,
            descriptions={chunk_id: description} if description else {},
            weight=weight,
            provenance={chunk_id},
        )
    else:
        relation.provenance.add(chunk_id)
        if description:
            relation.descriptions[chunk_id] = description
        refresh_description(relation)
        relation.weight = max(relation.weight, weight)
        relation.updated_at = utcnow()
    store.upsert_relation(relation)
    return relation


def refresh_description(relation: Relation) -> bool:
    """Re-derive a relation's description from its surviving provenance. See
    ``store.entities.refresh_description`` - same leak, same fix."""
    relation.descriptions = {
        chunk_id: text
        for chunk_id, text in relation.descriptions.items()
        if chunk_id in relation.provenance
    }
    best = max(relation.descriptions.values(), key=len, default="")
    if best == relation.description:
        return False
    relation.description = best
    relation.updated_at = utcnow()
    return True


def strip_chunks(store: GraphStore, chunk_ids: set[str]) -> tuple[list[str], list[str]]:
    """Remove these chunk ids from every relation's provenance.

    Returns ``(expired_relation_ids, weakened_relation_ids)``. A relation whose provenance
    empties is deleted - that is the whole deletion algorithm.
    """
    expired: list[str] = []
    weakened: list[str] = []
    for relation in store.relations_with_provenance(chunk_ids):
        remaining = relation.provenance - chunk_ids
        if remaining == relation.provenance:
            continue
        if not remaining:
            store.remove_relation(relation.relation_id)
            expired.append(relation.relation_id)
        else:
            relation.provenance = remaining
            refresh_description(relation)
            relation.updated_at = utcnow()
            store.upsert_relation(relation)
            weakened.append(relation.relation_id)
    return expired, weakened


def neighbours(store: GraphStore, entity_id: str) -> list[tuple[str, Relation]]:
    """One-hop neighbourhood as ``(other_entity_id, relation)``."""
    out: list[tuple[str, Relation]] = []
    for relation in store.relations_for_entity(entity_id):
        other = relation.target_id if relation.source_id == entity_id else relation.source_id
        out.append((other, relation))
    return out
