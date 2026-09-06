"""End-to-end smoke run: ingest -> query -> update -> delete, all offline.

Run with ``python scripts/smoke.py``. Prints what each stage did to the graph so a
regression in the churn engine is visible without reading a test failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from egraph.pipeline import build_pipeline  # noqa: E402
from egraph.schemas import DocumentChange, QueryRequest  # noqa: E402
from egraph.settings import Settings  # noqa: E402
from fixtures.mini_corpus import BASE_DOCS, DELETION_SET, UPDATED_DOCS  # noqa: E402


def main() -> int:
    settings = Settings(memory_store_path="", store_backend="memory", queue_backend="inline")
    pipeline = build_pipeline(settings)
    pipeline.reset()

    print("=== ingest ===")
    for doc_id, text in BASE_DOCS.items():
        result = pipeline.apply(
            DocumentChange(op="add", doc_id=doc_id, uri=f"{doc_id}.md", content=text)
        )
        print(f"  add {doc_id}: +{result.chunks_added} chunks, {result.sync_wall_ms:.1f} ms sync")

    report = pipeline.drain()
    stats = pipeline.stats()
    print(f"  summaries: {report.recomputed} recomputed")
    print(
        f"  index: {stats.entities} entities, {stats.relations} relations, "
        f"{stats.communities} communities, {stats.dirty_communities} dirty"
    )

    print("\n=== query ===")
    answer = pipeline.query(QueryRequest(question="Who founded Helios Labs?"))
    print(f"  A: {answer.text[:200]}")
    print(f"  citations: {answer.citations}")
    print(f"  stale communities touched: {answer.stale_communities_touched}")

    print("\n=== update (local edit) ===")
    before = pipeline.stats()
    result = pipeline.apply(
        DocumentChange(op="update", doc_id="northwind", content=UPDATED_DOCS["northwind"])
    )
    print(
        f"  chunks: +{result.chunks_added} new, {result.chunks_reused} reused, "
        f"-{result.chunks_removed} removed"
    )
    print(f"  dirty communities after update: {len(pipeline.store.dirty_communities())}")
    print(f"  sync latency: {result.sync_wall_ms:.1f} ms")
    report = pipeline.drain()
    print(f"  recomputed {report.recomputed} of {before.communities} communities")

    print("\n=== delete ===")
    for doc_id in DELETION_SET:
        result = pipeline.apply(DocumentChange(op="delete", doc_id=doc_id))
        gc = result.gc_summary
        print(
            f"  delete {doc_id}: {gc.relations_expired} relations expired, "
            f"{gc.relations_weakened} weakened, {gc.entities_removed} entities collected, "
            f"{gc.communities_marked_dirty} communities dirtied ({result.sync_wall_ms:.1f} ms)"
        )

    pipeline.drain()
    answer = pipeline.query(QueryRequest(question="Who founded Kestrel Systems?"))
    print(f"\n  after delete, 'Who founded Kestrel Systems?' -> {answer.text[:160]}")
    answer = pipeline.query(QueryRequest(question="Who founded Helios Labs?"))
    print(f"  surviving fact still answerable -> {answer.text[:160]}")

    stats = pipeline.stats()
    print(
        f"\n  final index: {stats.entities} entities, {stats.relations} relations, "
        f"{stats.communities} communities"
    )
    print(f"  cost: {pipeline.meter.snapshot()}")

    orphans = [e.entity_id for e in pipeline.store.all_entities() if not e.mentions]
    unsupported = [r.relation_id for r in pipeline.store.all_relations() if not r.provenance]
    print(f"  integrity: {len(orphans)} orphans, {len(unsupported)} unsupported relations")
    return 0 if not orphans and not unsupported else 1


if __name__ == "__main__":
    raise SystemExit(main())
