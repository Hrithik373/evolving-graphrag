"""Evaluation harness: time-split churn benchmark, baselines, metrics, Pareto sweep."""

from egraph.eval.baselines import ALL_SYSTEMS, System, SystemRegistry
from egraph.eval.benchmark import ChurnBenchmark, build_benchmark, build_scaled_benchmark
from egraph.eval.metrics import aggregate, score_question, token_f1
from egraph.eval.pareto import compaction_ablation, resolution_sweep, sweep
from egraph.eval.runner import EvalRun, render_report, run_full_eval, write_results
from egraph.eval.scenarios import run_scenarios

__all__ = [
    "ALL_SYSTEMS",
    "ChurnBenchmark",
    "EvalRun",
    "System",
    "SystemRegistry",
    "aggregate",
    "build_benchmark",
    "build_scaled_benchmark",
    "compaction_ablation",
    "render_report",
    "resolution_sweep",
    "run_full_eval",
    "run_scenarios",
    "score_question",
    "sweep",
    "token_f1",
    "write_results",
]
