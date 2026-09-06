"""Garbage collection - deletion as a graph walk, never a rebuild.

The algorithm, in full:

1. Take the chunk ids that are going away.
2. Strip those ids from every relation's ``provenance`` set. A relation whose set empties
   has no support left in the corpus and is deleted; one with support left survives,
   weakened.
3. Detach those chunk ids from every entity's mention set. An entity with no mentions left
   is an orphan and is collected - along with any relations still hanging off it.
4. A community whose members were all collected is itself removed; one that lost some
   members is marked dirty.
5. Return the receipt.

The invariant the property tests check: **anything still supported by a surviving chunk is
untouched.** Deletion may only remove what the deleted document uniquely supported.
"""

from __future__ import annotations

import logging

from egraph.churn import mutations as ops
from egraph.observability import metrics
from egraph.schemas import GCSummary, GraphMutation
from egraph.store import communities as community_ops
from egraph.store import entities as entity_ops
from egraph.store.base import GraphStore

log = logging.getLogger(__name__)


def collect(
    store: GraphStore, chunk_ids: set[str], remove_chunks: bool = True
) -> tuple[GCSummary, list[GraphMutation]]:
    """Retract the support of ``chunk_ids`` and collect whatever that leaves unsupported."""
    summary = GCSummary()
    emitted: list[GraphMutation] = []
    if not chunk_ids:
        return summary, emitted

    touched_communities: list[str] = []

    # Sorted iteration throughout: the receipt lists ids in a stable order, and the order
    # communities are first marked dirty decides which ones a bounded budget recomputes.
    ordered_chunks = sorted(chunk_ids)

    # 1-2. Withdraw relation support.
    for relation in sorted(store.relations_with_provenance(chunk_ids), key=lambda r: r.relation_id):
        mutation = ops.expire_relation(store, relation.relation_id, chunk_ids)
        if mutation is None:
            continue
        emitted.append(mutation)
        if mutation.payload.get("expired"):
            summary.relations_expired += 1
            summary.expired_relation_ids.append(relation.relation_id)
        else:
            summary.relations_weakened += 1
        for community_id in mutation.affected_communities:
            if community_id not in touched_communities:
                touched_communities.append(community_id)

    # 3. Detach mentions, then collect orphans.
    candidates: dict[str, None] = {}
    for chunk_id in ordered_chunks:
        for entity in sorted(store.entities_mentioning(chunk_id), key=lambda e: e.entity_id):
            candidates[entity.entity_id] = None
    for entity_id in candidates:
        entity = store.get_entity(entity_id)
        if entity is None:
            continue
        remaining = entity.mentions - chunk_ids
        if remaining == entity.mentions:
            continue
        entity.mentions = remaining
        if remaining:
            # The entity survives, but any description written out of a deleted chunk must
            # go with that chunk - otherwise a survivor keeps quoting a retracted document.
            description_changed = entity_ops.refresh_description(entity)
            if description_changed and entity.community_id not in (None, *touched_communities):
                touched_communities.append(entity.community_id)
            store.upsert_entity(entity)
            continue
        store.upsert_entity(entity)
        if entity.community_id and entity.community_id not in touched_communities:
            touched_communities.append(entity.community_id)
        mutation = ops.remove_entity(store, entity_id, reason="orphan_after_delete")
        if mutation is not None:
            emitted.append(mutation)
            summary.entities_removed += 1
            summary.removed_entity_ids.append(entity_id)

    # 4. Drop the chunks themselves, then reconcile communities.
    if remove_chunks:
        for chunk_id in ordered_chunks:
            if store.get_chunk(chunk_id) is not None:
                store.remove_chunk(chunk_id)
                summary.chunks_removed += 1

    removed_communities = community_ops.prune_empty(store)
    live = [c for c in touched_communities if c not in removed_communities]
    marked = community_ops.mark_dirty(store, live, reason="gc")
    summary.communities_marked_dirty = len(marked)

    log.info(
        "gc: %d chunks -> %d relations expired, %d weakened, %d entities collected",
        len(chunk_ids),
        summary.relations_expired,
        summary.relations_weakened,
        summary.entities_removed,
    )
    metrics.DOCUMENT_OPS.labels(op="gc", result="ok").inc()
    return summary, emitted


def sweep_orphans(store: GraphStore) -> list[GraphMutation]:
    """Belt-and-braces pass for entities left unsupported by any path.

    Under normal operation ``collect`` leaves none; this exists so a crashed worker or a
    partially-applied batch cannot leave the graph asserting facts nothing supports.
    """
    emitted: list[GraphMutation] = []
    for entity in store.all_entities():
        if entity.mentions:
            continue
        mutation = ops.remove_entity(store, entity.entity_id, reason="orphan_sweep")
        if mutation is not None:
            emitted.append(mutation)
    if emitted:
        community_ops.prune_empty(store)
        log.warning("orphan sweep collected %d entities", len(emitted))
    return emitted


def dangling_relations(store: GraphStore) -> list[str]:
    """Relations pointing at an entity that no longer exists. Should always be empty."""
    return [
        relation.relation_id
        for relation in store.all_relations()
        if store.get_entity(relation.source_id) is None
        or store.get_entity(relation.target_id) is None
    ]
