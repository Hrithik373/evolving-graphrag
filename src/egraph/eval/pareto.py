"""The cost-vs-freshness Pareto sweep - the headline figure.

The knob is the **recompute budget**: how many dirty community summaries the system is
allowed to regenerate per document change. Zero means never re-summarise (cheapest, most
stale); unbounded means drain the dirty set after every change (freshest, dearest). Full
reindex sits off the top-right of the plot as the reference point.

What the curve shows is the whole argument: freshness is not binary, it is purchasable in
small increments, and the price of the first increment is far below the price of a rebuild.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from egraph.eval.baselines import EvolvingSystem, FullReindexSystem
from egraph.eval.benchmark import ChurnBenchmark
from egraph.eval.scenarios import ask
from egraph.settings import Settings

log = logging.getLogger(__name__)


@dataclass
class ParetoPoint:
    label: str
    recompute_budget: int | None
    compaction_interval: int | None
    update_tokens: int
    update_usd: float
    update_wall_ms: float
    communities_recomputed: int
    dirty_fraction_after: float
    # Mean dirty community summaries used to answer a query. Unlike stale_answer_rate,
    # which is a property of the *graph* (and so is fixed once GC is correct), this is the
    # freshness the *reader* actually sees, and it is what the recompute budget buys.
    mean_stale_touched: float
    stale_answer_rate: float
    deletion_f1: float
    new_info_recall: float
    base_recall: float
    survivor_recall: float
    entities: int
    relations: int

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "recompute_budget": self.recompute_budget,
            "compaction_interval": self.compaction_interval,
            "update_tokens": self.update_tokens,
            "update_usd": round(self.update_usd, 6),
            "update_wall_ms": round(self.update_wall_ms, 2),
            "communities_recomputed": self.communities_recomputed,
            "dirty_fraction_after": round(self.dirty_fraction_after, 4),
            "mean_stale_touched": round(self.mean_stale_touched, 4),
            "stale_answer_rate": round(self.stale_answer_rate, 4),
            "deletion_f1": round(self.deletion_f1, 4),
            "new_info_recall": round(self.new_info_recall, 4),
            "base_recall": round(self.base_recall, 4),
            "survivor_recall": round(self.survivor_recall, 4),
            "entities": self.entities,
            "relations": self.relations,
        }


@dataclass
class ParetoResult:
    points: list[ParetoPoint] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"points": [p.as_dict() for p in self.points], "frontier": self.frontier()}

    def frontier(self) -> list[str]:
        """Points not dominated on both axes (cheaper *and* fresher)."""
        labels = []
        for point in self.points:
            dominated = any(
                other is not point
                and other.update_tokens <= point.update_tokens
                and other.mean_stale_touched <= point.mean_stale_touched
                and (
                    other.update_tokens < point.update_tokens
                    or other.mean_stale_touched < point.mean_stale_touched
                )
                for other in self.points
            )
            if not dominated:
                labels.append(point.label)
        return labels


def _run_point(
    settings: Settings,
    benchmark: ChurnBenchmark,
    budget: int | None,
    label: str,
) -> ParetoPoint:
    system = EvolvingSystem(settings, recompute_budget=budget)

    for change in benchmark.base_changes():
        system.apply(change)
    system.pipeline.drain()  # everyone starts from a fully-built, clean index

    system.reset_cost()
    for change in benchmark.churn_changes():
        system.apply(change)
    for change in benchmark.deletion_changes():
        system.apply(change)
    system.settle()

    cost = system.cost()
    staleness = system.pipeline.staleness()
    stats = system.stats()

    deletion, deletion_rows = ask(system, benchmark.deletion_questions)
    new_info, new_rows = ask(system, benchmark.new_questions)
    base, base_rows = ask(system, benchmark.base_questions)
    survivors, _ = ask(system, benchmark.surviving_questions)
    rows = deletion_rows + new_rows + base_rows

    point = ParetoPoint(
        label=label,
        recompute_budget=budget,
        compaction_interval=settings.compaction_interval_s if settings.compaction_enabled else None,
        update_tokens=cost.tokens,
        update_usd=cost.usd,
        update_wall_ms=cost.wall_ms,
        communities_recomputed=cost.communities_recomputed,
        dirty_fraction_after=staleness.dirty_fraction,
        mean_stale_touched=sum(r["stale_communities_touched"] for r in rows) / max(len(rows), 1),
        stale_answer_rate=deletion.stale_answer_rate,
        deletion_f1=deletion.f1,
        new_info_recall=new_info.recall,
        base_recall=base.recall,
        survivor_recall=survivors.recall,
        entities=stats.entities,
        relations=stats.relations,
    )
    system.pipeline.close()
    return point


def sweep(
    settings: Settings,
    benchmark: ChurnBenchmark,
    budgets: list[int | None] | None = None,
    include_reference: bool = True,
) -> ParetoResult:
    """Sweep the recompute budget and, optionally, add full-reindex as the reference point."""
    budgets = budgets if budgets is not None else [0, 1, 2, 4, 8, None]
    result = ParetoResult()

    for budget in budgets:
        label = "evolving/drain" if budget is None else f"evolving/budget={budget}"
        log.info("pareto point %s", label)
        result.points.append(_run_point(settings, benchmark, budget, label))

    if include_reference:
        result.points.append(_reference_point(settings, benchmark))
    return result


def _reference_point(settings: Settings, benchmark: ChurnBenchmark) -> ParetoPoint:
    """Full reindex: the freshness ceiling, priced honestly."""
    system = FullReindexSystem(settings)
    for change in benchmark.base_changes():
        system.apply(change)

    system.reset_cost()
    for change in benchmark.churn_changes():
        system.apply(change)
    for change in benchmark.deletion_changes():
        system.apply(change)
    system.settle()

    cost = system.cost()
    stats = system.stats()
    deletion, deletion_rows = ask(system, benchmark.deletion_questions)
    new_info, new_rows = ask(system, benchmark.new_questions)
    base, base_rows = ask(system, benchmark.base_questions)
    survivors, _ = ask(system, benchmark.surviving_questions)
    rows = deletion_rows + new_rows + base_rows

    point = ParetoPoint(
        label="full-reindex",
        recompute_budget=None,
        compaction_interval=None,
        update_tokens=cost.tokens,
        update_usd=cost.usd,
        update_wall_ms=cost.wall_ms,
        communities_recomputed=cost.communities_recomputed,
        dirty_fraction_after=system.pipeline.staleness().dirty_fraction,
        mean_stale_touched=sum(r["stale_communities_touched"] for r in rows) / max(len(rows), 1),
        stale_answer_rate=deletion.stale_answer_rate,
        deletion_f1=deletion.f1,
        new_info_recall=new_info.recall,
        base_recall=base.recall,
        survivor_recall=survivors.recall,
        entities=stats.entities,
        relations=stats.relations,
    )
    system.pipeline.close()
    return point


def compaction_ablation(
    settings: Settings, benchmark: ChurnBenchmark, intervals: list[int | None]
) -> list[dict]:
    """Quality with and without periodic compaction.

    ``None`` disables compaction entirely; an integer runs one compaction pass every N
    changes. Answers the question the risk register raises: does incremental placement
    drift far enough to matter, and does compaction pay for itself?
    """
    rows: list[dict] = []
    for interval in intervals:
        cfg = settings.model_copy(update={"compaction_enabled": interval is not None})
        system = EvolvingSystem(cfg, recompute_budget=None)
        for change in benchmark.base_changes():
            system.apply(change)
        system.pipeline.drain()

        system.reset_cost()
        changes = benchmark.churn_changes() + benchmark.deletion_changes()
        compactions = 0
        for index, change in enumerate(changes, start=1):
            system.apply(change)
            if interval is not None and index % interval == 0:
                system.pipeline.compact()
                system.pipeline.drain()
                compactions += 1
        system.settle()

        base, _ = ask(system, benchmark.base_questions)
        new_info, _ = ask(system, benchmark.new_questions)
        deletion, _ = ask(system, benchmark.deletion_questions)
        communities = system.pipeline.store.all_communities()
        sizes = [c.member_count for c in communities] or [0]

        rows.append(
            {
                "compaction_interval": interval,
                "compactions": compactions,
                "update_tokens": system.cost().tokens,
                "communities": len(communities),
                "mean_community_size": round(sum(sizes) / len(sizes), 2),
                "max_community_size": max(sizes),
                "base_recall": round(base.recall, 4),
                "new_info_recall": round(new_info.recall, 4),
                "stale_answer_rate": round(deletion.stale_answer_rate, 4),
            }
        )
        system.pipeline.close()
    return rows


def resolution_sweep(
    settings: Settings, benchmark: ChurnBenchmark, taus: list[float]
) -> list[dict]:
    """Entity-resolution threshold sweep - the mitigation named in the risk register."""
    rows: list[dict] = []
    for tau in taus:
        cfg = settings.model_copy(update={"tau_sim": tau})
        system = EvolvingSystem(cfg, recompute_budget=None)
        for change in benchmark.base_changes():
            system.apply(change)
        system.settle()

        decisions = system.pipeline.store.list_resolutions(limit=100_000)
        merges = sum(1 for d in decisions if d.decision == "merge")
        base, _ = ask(system, benchmark.base_questions)
        stats = system.stats()
        rows.append(
            {
                "tau_sim": tau,
                "entities": stats.entities,
                "relations": stats.relations,
                "merge_rate": round(merges / len(decisions), 4) if decisions else 0.0,
                "base_recall": round(base.recall, 4),
                "base_f1": round(base.f1, 4),
            }
        )
        system.pipeline.close()
    return rows
