"""Community membership and dirty-flag storage operations.

The policy (when to mark dirty, how many to recompute) lives in ``churn/dirty.py`` and
``summarize/recompute.py``; this module only knows how to write it down.
"""

from __future__ import annotations

import hashlib
import itertools

from egraph.schemas import Community, utcnow
from egraph.store.base import GraphStore

# Monotonic within a process, which is what makes a single-process eval run reproducible.
# Across api and worker processes the two counters interleave arbitrarily; that only
# affects the *order* dirty communities are drained in, never which ones are dirty, and
# oldest-first fairness still holds within each process.
_dirty_seq = itertools.count(1)


def next_dirty_seq() -> int:
    return next(_dirty_seq)


def summary_hash(title: str, summary: str) -> str:
    return hashlib.sha256(f"{title}\n{summary}".encode()).hexdigest()[:32]


def community_of(store: GraphStore, entity_id: str) -> str | None:
    entity = store.get_entity(entity_id)
    return entity.community_id if entity else None


def assign(store: GraphStore, entity_id: str, community_id: str) -> None:
    """Move an entity into a community, keeping both sides of the membership consistent."""
    entity = store.get_entity(entity_id)
    if entity is None:
        return
    previous = entity.community_id
    if previous == community_id:
        target = store.get_community(community_id)
        if target is not None and entity_id not in target.members:
            target.members.add(entity_id)
            store.upsert_community(target)
        return
    if previous:
        old = store.get_community(previous)
        if old is not None:
            old.members.discard(entity_id)
            mark_dirty(store, [previous], reason="member_left")
            store.upsert_community(old)
    entity.community_id = community_id
    entity.updated_at = utcnow()
    store.upsert_entity(entity)
    target = store.get_community(community_id)
    if target is None:
        target = Community(community_id=community_id, title=f"Community {community_id[:8]}")
    target.members.add(entity_id)
    if not target.dirty:
        target.dirty = True
        target.dirty_since = utcnow()
        target.dirty_seq = next_dirty_seq()
    store.upsert_community(target)


def mark_dirty(store: GraphStore, community_ids: list[str], reason: str = "") -> list[str]:
    """Flag communities for re-summarisation. Returns the ids that actually changed state."""
    changed: list[str] = []
    # Deduplicate while PRESERVING ORDER. A set comprehension here iterates in an order
    # that varies between processes (string hashing is randomised), which changes which
    # community gets the earliest `dirty_since` - and therefore which ones a bounded
    # recompute budget picks. That made the eval non-reproducible run to run.
    for community_id in dict.fromkeys(c for c in community_ids if c):
        community = store.get_community(community_id)
        if community is None or community.dirty:
            continue
        community.dirty = True
        community.dirty_since = utcnow()
        community.dirty_seq = next_dirty_seq()
        community.updated_at = utcnow()
        store.upsert_community(community)
        changed.append(community_id)
    return changed


def clear_dirty(store: GraphStore, community_id: str) -> None:
    community = store.get_community(community_id)
    if community is None:
        return
    community.dirty = False
    community.dirty_since = None
    community.dirty_seq = 0
    community.summarized_members = set(community.members)
    community.updated_at = utcnow()
    store.upsert_community(community)


def prune_empty(store: GraphStore) -> list[str]:
    """A community whose members were all collected is itself garbage."""
    removed = []
    for community in store.all_communities():
        live = {m for m in community.members if store.get_entity(m) is not None}
        if not live:
            store.remove_community(community.community_id)
            removed.append(community.community_id)
        elif live != community.members:
            community.members = live
            if not community.dirty:
                community.dirty = True
                community.dirty_since = utcnow()
                community.dirty_seq = next_dirty_seq()
            store.upsert_community(community)
    return removed
