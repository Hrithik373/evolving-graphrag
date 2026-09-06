"""The churn engine: incremental maintenance of the graph under document churn.

This package is the contribution. Everything here exists so that a document change touches
the minimal affected subgraph and the minimal set of community summaries.
"""

from egraph.churn.compact import CompactionReport, compact
from egraph.churn.diff import ChunkDiff, ExtractionDiff, diff_chunks, diff_extractions
from egraph.churn.dirty import DirtyTracker
from egraph.churn.engine import ChurnEngine
from egraph.churn.gc import collect, dangling_relations, sweep_orphans

__all__ = [
    "ChunkDiff",
    "ChurnEngine",
    "CompactionReport",
    "DirtyTracker",
    "ExtractionDiff",
    "collect",
    "compact",
    "dangling_relations",
    "diff_chunks",
    "diff_extractions",
    "sweep_orphans",
]
