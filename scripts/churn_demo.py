"""The live demo, driven against a running API.

Runs the sequence the review demo needs and prints what the index did at each step:

    seed  ->  query  ->  update one paragraph  ->  watch dirty spike and drain
          ->  delete a document  ->  ask the same question again

Have the Grafana "index maintenance" dashboard open beside it: the dirty-communities gauge
spikes on the write and drains as the worker recomputes only the affected summaries.

    python scripts/churn_demo.py --api http://localhost:8000 --pause 3
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.mini_corpus import BASE_DOCS, UPDATED_DOCS  # noqa: E402

RULE = "─" * 72


def rule(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def wait_for_drain(client: httpx.Client, timeout_s: float = 60.0) -> dict:
    """Poll staleness until the worker has caught up. This is the gauge draining."""
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        last = client.get("/index/staleness").json()
        print(
            f"    dirty={last['dirty_count']}/{last['community_count']} "
            f"({last['dirty_fraction'] * 100:.0f}%)  queue={last['queue_depth']}",
            end="\r",
        )
        if last["dirty_count"] == 0:
            print(" " * 70, end="\r")
            return last
        time.sleep(0.5)
    print()
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--pause", type=float, default=1.5, help="seconds between steps")
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="do not poll for the drain (use when no worker is running)",
    )
    args = parser.parse_args()

    client = httpx.Client(base_url=args.api.rstrip("/"), timeout=120.0)
    try:
        client.get("/health").raise_for_status()
    except httpx.HTTPError as exc:
        print(f"API not reachable at {args.api}: {exc}")
        print("start it with `make up` (docker) or `make api` (local)")
        return 1

    ready = client.get("/ready").json()
    print(
        f"connected: store={ready['store_backend']} queue={ready['queue_backend']} "
        f"llm={ready['llm_backend']}"
    )

    # ------------------------------------------------------------------ 1. seed
    rule("1. build the index")
    for doc_id, text in BASE_DOCS.items():
        body = client.post(
            "/documents", json={"uri": f"{doc_id}.md", "content": text, "doc_id": doc_id}
        ).json()
        print(
            f"  add {doc_id:18s} +{body['chunks_added']} chunks, "
            f"{body['sync_wall_ms']:.1f} ms synchronous"
        )
    if not args.no_wait:
        client.post("/maintenance/recompute", params={"drain": True})
    stats = client.get("/index/stats").json()
    print(
        f"\n  {stats['entities']} entities · {stats['relations']} relations · "
        f"{stats['communities']} communities · ${stats['total_usd']:.4f}"
    )
    time.sleep(args.pause)

    # ------------------------------------------------------------------ 2. query
    rule("2. ask a question")
    question = "Who leads the platform team at Northwind Analytics?"
    answer = client.post("/query", json={"question": question}).json()
    print(f"  Q  {question}")
    print(f"  A  {answer['text'][:260]}")
    print(
        f"     cited {answer['citations']}, {answer['stale_communities_touched']} stale summaries"
    )
    time.sleep(args.pause)

    # ------------------------------------------------------------------ 3. update
    rule("3. edit ONE paragraph of northwind")
    before = client.get("/index/staleness").json()
    body = client.put("/documents/northwind", json={"content": UPDATED_DOCS["northwind"]}).json()
    after = client.get("/index/staleness").json()
    print(
        f"  chunks: +{body['chunks_added']} new, {body['chunks_reused']} REUSED "
        f"(not re-extracted), -{body['chunks_removed']} removed"
    )
    print(f"  returned in {body['sync_wall_ms']:.1f} ms — the LLM work is queued, not inline")
    print(
        f"  dirty communities: {before['dirty_count']} -> {after['dirty_count']} "
        f"of {after['community_count']} — the rest keep their summaries"
    )
    if not args.no_wait:
        print("  waiting for the worker to drain the dirty set…")
        wait_for_drain(client)
        print("  drained.")
    else:
        client.post("/maintenance/recompute", params={"drain": True})
    time.sleep(args.pause)

    rule("4. the same question, after the edit")
    answer = client.post("/query", json={"question": question}).json()
    print(f"  A  {answer['text'][:260]}")
    time.sleep(args.pause)

    # ------------------------------------------------------------------ 5. delete
    rule("5. delete northwind entirely")
    body = client.request("DELETE", "/documents/northwind").json()
    gc = body["gc_summary"]
    print(f"  returned in {body['sync_wall_ms']:.1f} ms — deletion is a graph walk, not a rebuild")
    print(f"    relations expired : {gc['relations_expired']}  (no source left)")
    print(
        f"    relations weakened: {gc['relations_weakened']}  (another document still supports them)"
    )
    print(f"    entities collected: {gc['entities_removed']}  (orphaned)")
    print(f"    chunks removed    : {gc['chunks_removed']}")
    if not args.no_wait:
        wait_for_drain(client)
    else:
        client.post("/maintenance/recompute", params={"drain": True})
    time.sleep(args.pause)

    rule("6. ask again — the retracted fact is gone")
    answer = client.post("/query", json={"question": question}).json()
    print(f"  Q  {question}")
    print(f"  A  {answer['text'][:260]}")
    print(f"     cited {answer['citations'] or 'nothing'}")

    survivor = "Which protocol does Aurora Engine use for replication between clusters?"
    answer = client.post("/query", json={"question": survivor}).json()
    print(f"\n  Q  {survivor}   (supported by a document we did NOT delete)")
    print(f"  A  {answer['text'][:260]}")
    print("     still answerable — deletion removed only what northwind uniquely supported")

    rule("integrity")
    integrity = client.get("/maintenance/integrity").json()
    print(f"  invariants hold: {integrity['ok']}")
    print(f"    orphan entities            {len(integrity['orphan_entities'])}")
    print(f"    unsupported relations      {len(integrity['unsupported_relations'])}")
    print(f"    relations citing dead chunks {len(integrity['relations_citing_dead_chunks'])}")

    stats = client.get("/index/stats").json()
    print(
        f"\n  final index: {stats['entities']} entities · {stats['relations']} relations · "
        f"{stats['communities']} communities · ${stats['total_usd']:.4f} spent"
    )
    client.close()
    return 0 if integrity["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
