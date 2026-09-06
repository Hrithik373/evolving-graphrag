"""Load the mini-corpus into a running API (or the local store if none is up).

python scripts/seed.py                 # http://localhost:8000
python scripts/seed.py --api http://host:8000
python scripts/seed.py --local         # skip HTTP, write straight to the store
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.mini_corpus import BASE_DOCS  # noqa: E402


def seed_http(base_url: str) -> int:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"API not reachable at {base_url}: {exc}")
            return 1

        for doc_id, text in BASE_DOCS.items():
            response = client.post(
                "/documents", json={"uri": f"{doc_id}.md", "content": text, "doc_id": doc_id}
            )
            response.raise_for_status()
            body = response.json()
            print(
                f"  {body['op']:6s} {doc_id:18s} +{body['chunks_added']} chunks "
                f"({body['sync_wall_ms']:.1f} ms sync)"
            )

        report = client.post("/maintenance/recompute", params={"drain": True}).json()
        stats = client.get("/index/stats").json()
        print(f"\n  {report['recomputed']} summaries written, {report['skipped_clean']} left clean")
        print(
            f"  index: {stats['entities']} entities, {stats['relations']} relations, "
            f"{stats['communities']} communities"
        )
    return 0


def seed_local() -> int:
    from egraph.pipeline import build_pipeline
    from egraph.schemas import DocumentChange
    from egraph.settings import get_settings

    pipeline = build_pipeline(get_settings())
    for doc_id, text in BASE_DOCS.items():
        result = pipeline.apply(
            DocumentChange(op="add", doc_id=doc_id, uri=f"{doc_id}.md", content=text)
        )
        print(f"  {result.op:6s} {doc_id:18s} +{result.chunks_added} chunks")
    report = pipeline.drain()
    stats = pipeline.stats()
    print(f"\n  {report.recomputed} summaries written")
    print(
        f"  index: {stats.entities} entities, {stats.relations} relations, "
        f"{stats.communities} communities"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--local", action="store_true", help="write to the store directly")
    args = parser.parse_args()

    print(f"seeding {len(BASE_DOCS)} documents")
    return seed_local() if args.local else seed_http(args.api)


if __name__ == "__main__":
    raise SystemExit(main())
