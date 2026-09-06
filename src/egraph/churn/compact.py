"""Compaction - the LSM-tree analogy made literal.

Incremental placement is cheap but drifts: a long sequence of local decisions does not
reproduce what a global clustering would have produced. Compaction re-runs full community
detection off the hot path and reconciles the result with the existing communities.

The reconciliation is the interesting part. A naive implementation would drop every
community and re-summarise everything, which would cost exactly as much as a full reindex
and throw away the entire contribution. Instead compaction **matches new clusters to
existing communities by membership overlap** and only marks the ones whose membership
actually changed. A stable cluster keeps its id, its summary, and its clean flag.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from egraph.cluster.leiden import cluster
from egraph.cluster.placement import new_community_id
from egraph.observability import metrics
from egraph.schemas import Community, utcnow
from egraph.settings import Settings, get_settings
from egraph.store import communities as community_ops
from egraph.store.base import GraphStore

log = logging.getLogger(__name__)


class CompactionReport:
    def __init__(self) -> None:
        self.communities_before = 0
        self.communities_after = 0
        self.communities_created = 0
        self.communities_removed = 0
        self.communities_marked_dirty = 0
        self.entities_moved = 0
        self.stable_communities = 0
        self.wall_ms = 0.0
        self.backend = ""

    def as_dict(self) -> dict:
        return {
            "communities_before": self.communities_before,
            "communities_after": self.communities_after,
            "communities_created": self.communities_created,
            "communities_removed": self.communities_removed,
            "communities_marked_dirty": self.communities_marked_dirty,
            "entities_moved": self.entities_moved,
            "stable_communities": self.stable_communities,
            "wall_ms": round(self.wall_ms, 2),
            "backend": self.backend,
        }


def compact(store: GraphStore, settings: Settings | None = None) -> CompactionReport:
    cfg = settings or get_settings()
    started = time.perf_counter()
    report = CompactionReport()
    report.backend = cfg.cluster_backend

    before = store.all_communities()
    report.communities_before = len(before)

    membership = cluster(
        store, backend=cfg.cluster_backend, resolution=cfg.leiden_resolution, seed=cfg.seed
    )
    if not membership:
        report.wall_ms = (time.perf_counter() - started) * 1000
        return report

    clusters: dict[int, set[str]] = defaultdict(set)
    for entity_id, label in membership.items():
        clusters[label].add(entity_id)

    old_members = {c.community_id: set(c.members) for c in before}
    claimed: set[str] = set()
    assignment: dict[int, str] = {}

    # Match each new cluster to the existing community it overlaps most, greedily and by
    # descending overlap, so a cluster that barely moved keeps its identity and summary.
    scored: list[tuple[int, str, int]] = []
    for label, members in clusters.items():
        for community_id, previous in old_members.items():
            overlap = len(members & previous)
            if overlap:
                scored.append((label, community_id, overlap))
    scored.sort(key=lambda row: (-row[2], row[0], row[1]))
    for label, community_id, _ in scored:
        if label in assignment or community_id in claimed:
            continue
        assignment[label] = community_id
        claimed.add(community_id)

    for label, members in clusters.items():
        community_id = assignment.get(label) or new_community_id(f"compact:{min(members)}")
        community = store.get_community(community_id)
        if community is None:
            community = Community(
                community_id=community_id,
                level=0,
                title="",
                dirty=True,
                dirty_since=utcnow(),
                dirty_seq=community_ops.next_dirty_seq(),
            )
            report.communities_created += 1
        previous_members = set(community.members)
        if previous_members == members:
            report.stable_communities += 1
        community.members = set(members)
        community.member_count = len(members)
        community.updated_at = utcnow()
        # Only a real membership change invalidates the standing summary.
        if members != community.summarized_members and not community.dirty:
            community.dirty = True
            community.dirty_since = utcnow()
            community.dirty_seq = community_ops.next_dirty_seq()
            report.communities_marked_dirty += 1
        store.upsert_community(community)

        for entity_id in members:
            entity = store.get_entity(entity_id)
            if entity is None or entity.community_id == community_id:
                continue
            entity.community_id = community_id
            entity.updated_at = utcnow()
            store.upsert_entity(entity)
            report.entities_moved += 1

    # Communities no cluster claimed have lost all their members.
    for community_id in old_members:
        if community_id in claimed:
            continue
        store.remove_community(community_id)
        report.communities_removed += 1

    community_ops.prune_empty(store)
    report.communities_after = len(store.all_communities())
    report.wall_ms = (time.perf_counter() - started) * 1000
    metrics.COMPACTIONS.inc()
    metrics.COMPACTION_DURATION.observe(report.wall_ms / 1000)
    log.info("compaction: %s", report.as_dict())
    return report
