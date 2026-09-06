"""Incremental community placement - the cheap half of the LSM analogy.

A new entity is placed into an existing community by weighted neighbour vote, in O(degree),
with no global re-clustering. That is what keeps a single-document write off the critical
path of a full Leiden run. Placement drifts over time (chains of local decisions do not
reproduce a global optimum), and that drift is what periodic compaction pays off.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from egraph.schemas import Community, utcnow
from egraph.store.base import GraphStore
from egraph.store.communities import assign, next_dirty_seq


def new_community_id(seed: str) -> str:
    return "c-" + hashlib.sha256(seed.encode()).hexdigest()[:12]


def place(store: GraphStore, entity_id: str, max_size: int = 24) -> str:
    """Assign ``entity_id`` to a community and return that community id.

    Vote: each neighbour contributes its relation weight to its own community. The winner
    takes the entity, unless it is already at ``max_size`` - in which case the entity seeds
    a new community and compaction sorts out the structure later.
    """
    entity = store.get_entity(entity_id)
    if entity is None:
        return ""

    votes: dict[str, float] = defaultdict(float)
    for relation in store.relations_for_entity(entity_id):
        other_id = relation.target_id if relation.source_id == entity_id else relation.source_id
        other = store.get_entity(other_id)
        if other is None or not other.community_id:
            continue
        votes[other.community_id] += relation.weight * (1 + len(relation.provenance))

    for community_id, _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
        community = store.get_community(community_id)
        if community is None:
            continue
        if len(community.members) >= max_size and entity_id not in community.members:
            continue
        assign(store, entity_id, community_id)
        return community_id

    community_id = entity.community_id or new_community_id(entity_id)
    if store.get_community(community_id) is None:
        store.upsert_community(
            Community(
                community_id=community_id,
                level=0,
                title=entity.canonical_name,
                dirty=True,
                dirty_since=utcnow(),
                dirty_seq=next_dirty_seq(),
            )
        )
    assign(store, entity_id, community_id)
    return community_id


def place_all(store: GraphStore, entity_ids: list[str], max_size: int = 24) -> dict[str, str]:
    """Place a batch, highest-degree first so hubs anchor the communities their
    low-degree neighbours then join."""
    # Tie-break on the id: without it, equal-degree entities are placed in whatever order
    # the caller's set happened to iterate, and community assignment stops being reproducible.
    ranked = sorted(
        entity_ids,
        key=lambda eid: (-len(store.relations_for_entity(eid)), eid),
    )
    return {entity_id: place(store, entity_id, max_size) for entity_id in ranked}
