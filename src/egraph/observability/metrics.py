"""Prometheus signals - the demo dashboard and the report figures read from here.

Everything is registered against the default registry so ``/metrics`` on the API and the
worker's own exporter both expose the same names.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from egraph.schemas import CostRecord

# --- freshness ---------------------------------------------------------------------
DIRTY_COMMUNITIES = Gauge(
    "egraph_dirty_communities", "Communities awaiting re-summarisation right now"
)
DIRTY_FRACTION = Gauge("egraph_dirty_fraction", "Dirty communities / total communities")
OLDEST_DIRTY_AGE = Gauge(
    "egraph_oldest_dirty_age_seconds", "Age of the longest-waiting dirty community"
)

# --- write path --------------------------------------------------------------------
UPDATE_LATENCY = Histogram(
    "egraph_update_latency_ms",
    "Synchronous write-path latency by operation",
    ["op"],
    buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 5000),
)
MUTATIONS = Counter("egraph_mutations_total", "Graph mutations emitted", ["kind"])
DOCUMENT_OPS = Counter("egraph_document_ops_total", "Document operations", ["op", "result"])

# --- selective recompute (the contribution) -----------------------------------------
SUMMARY_RECOMPUTE = Counter(
    "egraph_summary_recompute_total", "Community summaries recomputed", ["trigger"]
)
COMMUNITIES_SKIPPED = Counter(
    "egraph_summary_skipped_total", "Clean communities left untouched by a recompute pass"
)
COMPACTIONS = Counter("egraph_compactions_total", "Full Leiden re-clustering passes")
COMPACTION_DURATION = Histogram(
    "egraph_compaction_seconds",
    "Wall time of a compaction pass",
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 15, 60),
)

# --- cost ----------------------------------------------------------------------------
LLM_TOKENS = Counter("egraph_llm_tokens_total", "Tokens consumed", ["op", "direction"])
LLM_USD = Counter("egraph_llm_usd_total", "USD spent", ["op"])
LLM_LATENCY = Histogram(
    "egraph_llm_latency_ms",
    "Model call latency",
    ["op"],
    buckets=(1, 5, 10, 50, 100, 500, 1000, 5000, 30000),
)
EXTRACTION_CACHE = Counter("egraph_extraction_cache_total", "Extraction cache lookups", ["result"])

# --- index size -----------------------------------------------------------------------
INDEX_ENTITIES = Gauge("egraph_index_entities", "Entities in the index")
INDEX_RELATIONS = Gauge("egraph_index_relations", "Relations in the index")
INDEX_COMMUNITIES = Gauge("egraph_index_communities", "Communities in the index")
INDEX_CHUNKS = Gauge("egraph_index_chunks", "Chunks in the index")
INDEX_DOCUMENTS = Gauge("egraph_index_documents", "Active documents in the index")

# --- queue -----------------------------------------------------------------------------
QUEUE_DEPTH = Gauge("egraph_queue_depth", "Jobs waiting in the worker queue")
JOBS = Counter("egraph_jobs_total", "Worker jobs", ["task", "result"])

# --- query ------------------------------------------------------------------------------
QUERY_LATENCY = Histogram(
    "egraph_query_latency_ms",
    "Query latency",
    ["mode"],
    buckets=(1, 5, 10, 25, 50, 100, 250, 1000, 5000),
)
STALE_TOUCHED = Histogram(
    "egraph_stale_communities_touched",
    "Dirty communities used to answer a query",
    buckets=(0, 1, 2, 3, 5, 10),
)


def observe_cost(record: CostRecord) -> None:
    LLM_TOKENS.labels(op=record.operation, direction="in").inc(record.tokens_in)
    LLM_TOKENS.labels(op=record.operation, direction="out").inc(record.tokens_out)
    LLM_USD.labels(op=record.operation).inc(record.usd)
    LLM_LATENCY.labels(op=record.operation).observe(record.wall_ms)
    if record.cache_hit:
        EXTRACTION_CACHE.labels(result="hit").inc()


def observe_index(stats) -> None:
    """Refresh the index-size gauges from an :class:`IndexStats`."""
    INDEX_ENTITIES.set(stats.entities)
    INDEX_RELATIONS.set(stats.relations)
    INDEX_COMMUNITIES.set(stats.communities)
    INDEX_CHUNKS.set(stats.chunks)
    INDEX_DOCUMENTS.set(stats.active_documents)
    DIRTY_COMMUNITIES.set(stats.dirty_communities)
    if stats.communities:
        DIRTY_FRACTION.set(stats.dirty_communities / stats.communities)
    else:
        DIRTY_FRACTION.set(0.0)


def observe_staleness(staleness) -> None:
    DIRTY_COMMUNITIES.set(staleness.dirty_count)
    DIRTY_FRACTION.set(staleness.dirty_fraction)
    OLDEST_DIRTY_AGE.set(staleness.oldest_dirty_age_s)
    QUEUE_DEPTH.set(staleness.queue_depth)
