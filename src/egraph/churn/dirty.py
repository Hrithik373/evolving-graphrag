"""Dirty tracking and the staleness metric.

A community is dirty when at least one of its members changed since its summary was
written. Only dirty communities get re-summarised - that is the cost lever - and the
staleness figures here are what the API, the Grafana gauge and the Pareto curve all read.
"""

from __future__ import annotations

import logging

from egraph.observability import metrics
from egraph.schemas import Community, GraphMutation, Staleness, utcnow
from egraph.store import communities as community_ops
from egraph.store.base import GraphStore

log = logging.getLogger(__name__)


class DirtyTracker:
    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def mark(self, community_ids: list[str], reason: str = "mutation") -> list[str]:
        changed = community_ops.mark_dirty(self.store, community_ids, reason=reason)
        if changed:
            log.debug("marked %d communities dirty (%s)", len(changed), reason)
        self._publish()
        return changed

    def mark_from_mutations(self, mutations: list[GraphMutation]) -> list[str]:
        ids: list[str] = []
        for mutation in mutations:
            for community_id in mutation.affected_communities:
                if community_id not in ids:
                    ids.append(community_id)
        return self.mark(ids, reason="mutation")

    def clear(self, community_id: str) -> None:
        community_ops.clear_dirty(self.store, community_id)
        self._publish()

    def next_batch(self, batch_size: int) -> list[Community]:
        """Oldest-dirty-first, so no community can starve behind a hot one."""
        return self.store.dirty_communities(limit=batch_size)

    def staleness(self, queue_depth: int = 0) -> Staleness:
        communities = self.store.all_communities()
        dirty = [c for c in communities if c.dirty]
        now = utcnow()
        oldest = 0.0
        for community in dirty:
            since = community.dirty_since or community.updated_at
            if since.tzinfo is None:  # tolerate stores that drop tzinfo
                continue
            oldest = max(oldest, (now - since).total_seconds())
        staleness = Staleness(
            dirty_count=len(dirty),
            community_count=len(communities),
            dirty_fraction=(len(dirty) / len(communities)) if communities else 0.0,
            oldest_dirty_age_s=round(oldest, 3),
            queue_depth=queue_depth,
        )
        metrics.observe_staleness(staleness)
        return staleness

    def _publish(self) -> None:
        communities = self.store.all_communities()
        dirty = sum(1 for c in communities if c.dirty)
        metrics.DIRTY_COMMUNITIES.set(dirty)
        metrics.DIRTY_FRACTION.set(dirty / len(communities) if communities else 0.0)
