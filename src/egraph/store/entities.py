"""Provenance-aware entity operations.

Nothing here creates graph state without a chunk id attached - that is the golden rule.
"""

from __future__ import annotations

from egraph.schemas import Entity, utcnow
from egraph.store.base import GraphStore


def refresh_description(entity: Entity) -> bool:
    """Re-derive ``description`` from the chunks that still support the entity.

    Descriptions are derived state: the LLM wrote them out of a particular chunk. If that
    chunk is deleted the sentence has no source any more, and leaving it in place is a
    provenance leak - the entity keeps asserting prose from a document that was retracted.
    Returns True when the visible description changed.
    """
    entity.descriptions = {
        chunk_id: text
        for chunk_id, text in entity.descriptions.items()
        if chunk_id in entity.mentions
    }
    best = max(entity.descriptions.values(), key=len, default="")
    if best == entity.description:
        return False
    entity.description = best
    entity.updated_at = utcnow()
    return True


def attach_mention(store: GraphStore, entity: Entity, chunk_id: str) -> Entity:
    """Record that ``chunk_id`` mentions this entity. Idempotent."""
    if chunk_id in entity.mentions:
        return entity
    entity.mentions.add(chunk_id)
    entity.updated_at = utcnow()
    store.upsert_entity(entity)
    return entity


def detach_mention(store: GraphStore, entity_id: str, chunk_id: str) -> bool:
    """Drop one mention. Returns True if the entity is now an orphan (zero mentions)."""
    entity = store.get_entity(entity_id)
    if entity is None:
        return False
    if chunk_id in entity.mentions:
        entity.mentions.discard(chunk_id)
        entity.updated_at = utcnow()
        store.upsert_entity(entity)
    return not entity.mentions


def merge_into(store: GraphStore, target_id: str, source: Entity) -> Entity | None:
    """Fold ``source`` into the entity ``target_id``: union mentions, aliases, description.

    The resolver decides *whether* to merge; this decides *how*, and it never loses
    provenance - the surviving entity carries the union of both mention sets.
    """
    target = store.get_entity(target_id)
    if target is None:
        return None
    target.mentions |= source.mentions
    target.aliases |= source.aliases
    if source.canonical_name.lower() != target.canonical_name.lower():
        target.aliases.add(source.canonical_name)
    target.descriptions.update(source.descriptions)
    refresh_description(target)
    if not target.embedding and source.embedding:
        target.embedding = source.embedding
    target.updated_at = utcnow()
    store.upsert_entity(target)
    return target


def refresh_degree(store: GraphStore, entity_id: str) -> int:
    entity = store.get_entity(entity_id)
    if entity is None:
        return 0
    entity.degree = len(store.relations_for_entity(entity_id))
    store.upsert_entity(entity)
    return entity.degree


def orphans(store: GraphStore) -> list[Entity]:
    """Entities no surviving chunk mentions any more."""
    return [e for e in store.all_entities() if not e.mentions]
