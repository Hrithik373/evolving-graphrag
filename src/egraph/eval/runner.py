"""Eval orchestration: one command regenerates every figure from scratch.

Every run is seeded and stamped with a ``config_hash``, and writes:

    results/summary.json          - everything, machine-readable
    results/scenarios.csv         - the main comparison table
    results/pareto.csv            - the cost-vs-freshness curve
    results/compaction.csv        - the compaction ablation
    results/resolution.csv        - the tau_sim sweep
    results/scaling.csv           - update cost vs corpus size
    results/report.md             - the tables, formatted for the write-up
"""

from __future__ import annotations

import csv
import json
import logging
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from egraph import __version__
from egraph.eval.baselines import ALL_SYSTEMS, EvolvingSystem, SystemRegistry
from egraph.eval.benchmark import ChurnBenchmark, build_benchmark, build_scaled_benchmark
from egraph.eval.pareto import compaction_ablation, resolution_sweep, sweep
from egraph.eval.scenarios import ScenarioOutcome, run_scenarios
from egraph.settings import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class EvalRun:
    config_hash: str
    seed: int
    started_at: float
    scenarios: list[ScenarioOutcome] = field(default_factory=list)
    pareto: dict = field(default_factory=dict)
    compaction: list[dict] = field(default_factory=list)
    resolution: list[dict] = field(default_factory=list)
    scaling: list[dict] = field(default_factory=list)
    benchmark_size: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    wall_s: float = 0.0

    def as_dict(self) -> dict:
        return {
            "config_hash": self.config_hash,
            "seed": self.seed,
            "version": __version__,
            "wall_s": round(self.wall_s, 2),
            "benchmark": self.benchmark_size,
            "environment": self.environment,
            "scenarios": [s.as_dict() for s in self.scenarios],
            "pareto": self.pareto,
            "compaction": self.compaction,
            "resolution": self.resolution,
            "scaling": self.scaling,
        }


def scaling_study(settings: Settings, copies: list[int]) -> list[dict]:
    """Update cost and index size as the corpus grows.

    The property being checked: the cost of one document change stays flat as the corpus
    grows, while a full reindex grows with it. That is the asymptotic form of the argument,
    and the memory-growth figure comes from the same rows.
    """
    rows: list[dict] = []
    for copy_count in copies:
        benchmark = build_scaled_benchmark(copy_count)
        system = EvolvingSystem(settings, recompute_budget=None)
        for change in benchmark.base_changes():
            system.apply(change)
        system.settle()
        build = system.cost().as_dict()

        # Price exactly one document change against the built index.
        system.reset_cost()
        one_change = benchmark.churn_changes()[0]
        system.apply(one_change)
        system.settle()
        incremental = system.cost().as_dict()

        stats = system.stats()
        rows.append(
            {
                "copies": copy_count,
                "documents": len(benchmark.base),
                "entities": stats.entities,
                "relations": stats.relations,
                "communities": stats.communities,
                "chunks": stats.chunks,
                "build_tokens": build["tokens"],
                "one_update_tokens": incremental["tokens"],
                "one_update_wall_ms": incremental["wall_ms"],
                "one_update_communities_recomputed": incremental["communities_recomputed"],
                "reindex_cost_ratio": (
                    round(build["tokens"] / incremental["tokens"], 2)
                    if incremental["tokens"]
                    else None
                ),
            }
        )
        system.pipeline.close()
    return rows


def run_full_eval(
    settings: Settings | None = None,
    systems: list[str] | None = None,
    quick: bool = False,
) -> EvalRun:
    cfg = settings or get_settings()
    started = time.perf_counter()
    benchmark: ChurnBenchmark = build_benchmark()

    run = EvalRun(
        config_hash=cfg.config_hash,
        seed=cfg.seed,
        started_at=time.time(),
        benchmark_size=benchmark.size,
        environment={
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "store_backend": cfg.store_backend,
            "llm_backend": cfg.llm_backend,
            "llm_model": cfg.llm_model,
            "embed_backend": cfg.embed_backend,
            "cluster_backend": _active_cluster_backend(cfg),
        },
    )

    names = systems or ALL_SYSTEMS
    log.info("running scenarios for %s", ", ".join(names))
    for name, system in SystemRegistry(cfg).build(names).items():
        log.info("  system %s", name)
        run.scenarios.extend(run_scenarios(system, benchmark))
        pipeline = getattr(system, "pipeline", None)
        if pipeline is not None:
            pipeline.close()

    log.info("running the cost-vs-freshness sweep")
    budgets: list[int | None] = [0, 1, None] if quick else [0, 1, 2, 4, 8, None]
    run.pareto = sweep(cfg, benchmark, budgets).as_dict()

    log.info("running the compaction ablation")
    run.compaction = compaction_ablation(
        cfg, benchmark, [None, 2, 4] if quick else [None, 1, 2, 4, 8]
    )

    log.info("running the entity-resolution threshold sweep")
    run.resolution = resolution_sweep(
        cfg, benchmark, [0.70, 0.86, 0.95] if quick else [0.60, 0.70, 0.80, 0.86, 0.92, 0.98]
    )

    log.info("running the scaling study")
    run.scaling = scaling_study(cfg, [1, 2] if quick else [1, 2, 4, 8])

    run.wall_s = time.perf_counter() - started
    return run


def _active_cluster_backend(cfg: Settings) -> str:
    from egraph.cluster.leiden import active_backend

    return active_backend(cfg.cluster_backend)


# ---------------------------------------------------------------------- output
def write_results(run: EvalRun, output_dir: str | Path) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    payload = run.as_dict()
    summary = out / "summary.json"
    summary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    written["summary"] = summary

    written["scenarios"] = _write_csv(
        out / "scenarios.csv",
        [
            {
                "system": s.system,
                "scenario": s.scenario,
                "n": s.scores.n,
                "f1": round(s.scores.f1, 4),
                "em": round(s.scores.em, 4),
                "recall": round(s.scores.recall, 4),
                "stale_answer_rate": round(s.scores.stale_answer_rate, 4),
                "abstention_rate": round(s.scores.abstention_rate, 4),
                "update_tokens": s.update_cost.get("tokens", 0),
                "update_wall_ms": s.update_cost.get("wall_ms", 0),
                "communities_recomputed": s.update_cost.get("communities_recomputed", 0),
                "entities": s.index.get("entities", 0),
                "relations": s.index.get("relations", 0),
                "communities": s.index.get("communities", 0),
            }
            for s in run.scenarios
        ],
    )
    written["pareto"] = _write_csv(out / "pareto.csv", run.pareto.get("points", []))
    written["compaction"] = _write_csv(out / "compaction.csv", run.compaction)
    written["resolution"] = _write_csv(out / "resolution.csv", run.resolution)
    written["scaling"] = _write_csv(out / "scaling.csv", run.scaling)
    report = out / "report.md"
    report.write_text(render_report(run), encoding="utf-8")
    written["report"] = report
    return written


def _write_csv(path: Path, rows: list[dict]) -> Path:
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_report(run: EvalRun) -> str:
    """Markdown tables, ready to paste into the write-up."""
    parts: list[str] = [
        "# evolving-graphrag - evaluation report",
        "",
        f"- config_hash: `{run.config_hash}`  seed: `{run.seed}`  version: `{__version__}`",
        f"- wall clock: {run.wall_s:.1f}s",
        f"- environment: {run.environment}",
        f"- benchmark: {run.benchmark_size}",
        "",
        "## 1. Scenario comparison",
        "",
        "`stale_answer_rate` on the **deletion** row is the headline: the fraction of",
        "questions still answered with a fact that only a deleted document supported.",
        "`deletion-survivors` is the control - facts a surviving document still supports",
        "must remain answerable, so a system cannot win by deleting too much.",
        "",
    ]

    by_scenario: dict[str, list[ScenarioOutcome]] = {}
    for outcome in run.scenarios:
        by_scenario.setdefault(outcome.scenario, []).append(outcome)

    for scenario, outcomes in by_scenario.items():
        parts += [
            f"### {scenario}",
            "",
            _table(
                [
                    "system",
                    "n",
                    "F1",
                    "recall",
                    "stale rate",
                    "update tokens",
                    "recomputed",
                    "entities",
                ],
                [
                    [
                        o.system,
                        o.scores.n,
                        f"{o.scores.f1:.3f}",
                        f"{o.scores.recall:.3f}",
                        f"{o.scores.stale_answer_rate:.3f}",
                        o.update_cost.get("tokens", 0),
                        o.update_cost.get("communities_recomputed", 0),
                        o.index.get("entities", 0),
                    ]
                    for o in outcomes
                ],
            ),
            "",
        ]

    points = run.pareto.get("points", [])
    if points:
        parts += [
            "## 2. Cost vs freshness",
            "",
            "The recompute budget is how many dirty community summaries may be regenerated",
            "per document change. Full reindex is the reference point, not a sweep point.",
            "",
            _table(
                [
                    "configuration",
                    "update tokens",
                    "recomputed",
                    "dirty after",
                    "stale summaries/query",
                    "stale rate",
                    "new-info recall",
                    "survivor recall",
                ],
                [
                    [
                        p["label"],
                        p["update_tokens"],
                        p["communities_recomputed"],
                        p["dirty_fraction_after"],
                        p["mean_stale_touched"],
                        p["stale_answer_rate"],
                        p["new_info_recall"],
                        p["survivor_recall"],
                    ]
                    for p in points
                ],
            ),
            "",
            f"Pareto frontier: {', '.join(run.pareto.get('frontier', []))}",
            "",
        ]

    if run.compaction:
        parts += [
            "## 3. Compaction ablation",
            "",
            "Does incremental placement drift far enough to matter, and does periodic",
            "re-clustering pay for itself?",
            "",
            _table(
                [
                    "interval",
                    "compactions",
                    "tokens",
                    "communities",
                    "mean size",
                    "max size",
                    "base recall",
                    "stale rate",
                ],
                [
                    [
                        r["compaction_interval"] if r["compaction_interval"] is not None else "off",
                        r["compactions"],
                        r["update_tokens"],
                        r["communities"],
                        r["mean_community_size"],
                        r["max_community_size"],
                        r["base_recall"],
                        r["stale_answer_rate"],
                    ]
                    for r in run.compaction
                ],
            ),
            "",
        ]

    if run.resolution:
        parts += [
            "## 4. Entity-resolution threshold sweep",
            "",
            _table(
                ["tau_sim", "entities", "relations", "merge rate", "base recall", "base F1"],
                [
                    [
                        r["tau_sim"],
                        r["entities"],
                        r["relations"],
                        r["merge_rate"],
                        r["base_recall"],
                        r["base_f1"],
                    ]
                    for r in run.resolution
                ],
            ),
            "",
        ]

    if run.scaling:
        parts += [
            "## 5. Scaling: update cost vs corpus size",
            "",
            "`one_update_tokens` should stay roughly flat while `build_tokens` grows with",
            "the corpus - that ratio is the asymptotic form of the whole argument.",
            "",
            _table(
                [
                    "copies",
                    "documents",
                    "entities",
                    "relations",
                    "build tokens",
                    "one update tokens",
                    "reindex/update ratio",
                ],
                [
                    [
                        r["copies"],
                        r["documents"],
                        r["entities"],
                        r["relations"],
                        r["build_tokens"],
                        r["one_update_tokens"],
                        r["reindex_cost_ratio"],
                    ]
                    for r in run.scaling
                ],
            ),
            "",
        ]

    return "\n".join(parts) + "\n"
