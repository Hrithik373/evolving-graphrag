"""Selective recompute - the cost lever.

Only communities with at least one changed member are re-summarised, in bounded batches.
Everything else is left alone. ``summary_recompute_total`` and ``summary_skipped_total``
are exported so the "few, not all" claim is measured rather than asserted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from egraph.churn.dirty import DirtyTracker
from egraph.observability import metrics
from egraph.settings import Settings, get_settings
from egraph.store.base import GraphStore
from egraph.summarize.summarizer import Summarizer

log = logging.getLogger(__name__)


@dataclass
class RecomputeReport:
    recomputed: int = 0
    remaining_dirty: int = 0
    skipped_clean: int = 0
    community_ids: list[str] = field(default_factory=list)
    tokens: int = 0
    usd: float = 0.0
    wall_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "recomputed": self.recomputed,
            "remaining_dirty": self.remaining_dirty,
            "skipped_clean": self.skipped_clean,
            "community_ids": self.community_ids,
            "tokens": self.tokens,
            "usd": round(self.usd, 6),
            "wall_ms": round(self.wall_ms, 2),
        }


class SummaryRecomputer:
    def __init__(
        self,
        store: GraphStore,
        summarizer: Summarizer,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.summarizer = summarizer
        self.settings = settings or get_settings()
        self.dirty = DirtyTracker(store)

    def recompute_dirty(
        self, batch_size: int | None = None, trigger: str = "worker"
    ) -> RecomputeReport:
        # `or` would turn an explicit budget of 0 back into the default, which silently
        # deletes the cheapest point on the Pareto curve.
        size = self.settings.dirty_batch_size if batch_size is None else batch_size
        report = RecomputeReport()
        meter = self.summarizer.llm.meter
        before = meter.snapshot()

        batch = self.dirty.next_batch(size) if size > 0 else []
        all_communities = self.store.all_communities()
        report.skipped_clean = len(all_communities) - sum(1 for c in all_communities if c.dirty)
        metrics.COMMUNITIES_SKIPPED.inc(max(0, report.skipped_clean))

        for community in batch:
            self.summarizer.summarise(community)
            report.recomputed += 1
            report.community_ids.append(community.community_id)
            metrics.SUMMARY_RECOMPUTE.labels(trigger=trigger).inc()

        after = meter.snapshot()
        report.tokens = int(after["tokens"] - before["tokens"])
        report.usd = float(after["usd"]) - float(before["usd"])
        report.wall_ms = float(after["wall_ms"]) - float(before["wall_ms"])
        report.remaining_dirty = len(self.store.dirty_communities())

        self.dirty.staleness()
        metrics.observe_index(self.store.stats())
        if report.recomputed:
            log.info("recomputed %d dirty communities: %s", report.recomputed, report.as_dict())
        return report

    def drain(self, max_batches: int = 100, trigger: str = "drain") -> RecomputeReport:
        """Recompute until nothing is dirty. Used by the demo, the eval and `make ingest`;
        the production path is one bounded batch per worker tick."""
        total = RecomputeReport()
        for _ in range(max_batches):
            report = self.recompute_dirty(trigger=trigger)
            total.recomputed += report.recomputed
            total.community_ids.extend(report.community_ids)
            total.tokens += report.tokens
            total.usd += report.usd
            total.wall_ms += report.wall_ms
            if report.recomputed == 0 or report.remaining_dirty == 0:
                break
        total.remaining_dirty = len(self.store.dirty_communities())
        total.skipped_clean = max(0, len(self.store.all_communities()) - total.recomputed)
        return total
