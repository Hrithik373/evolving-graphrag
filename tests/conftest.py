"""Shared fixtures. Every test runs fully offline: memory store, inline queue, mock LLM."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from egraph.pipeline import Pipeline, build_pipeline  # noqa: E402
from egraph.schemas import DocumentChange  # noqa: E402
from egraph.settings import Settings  # noqa: E402
from fixtures.mini_corpus import BASE_DOCS  # noqa: E402


def make_settings(**overrides) -> Settings:
    base = {
        "store_backend": "memory",
        "queue_backend": "inline",
        "llm_backend": "mock",
        "embed_backend": "hash",
        "memory_store_path": "",  # never touch disk from a test
        "compaction_enabled": False,
        "seed": 1337,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def pipeline(settings: Settings) -> Pipeline:
    pipe = build_pipeline(settings)
    pipe.reset()
    yield pipe
    pipe.close()


def ingest(pipeline: Pipeline, docs: dict[str, str]) -> None:
    for doc_id, text in docs.items():
        pipeline.apply(DocumentChange(op="add", doc_id=doc_id, uri=f"{doc_id}.md", content=text))


@pytest.fixture
def loaded(pipeline: Pipeline) -> Pipeline:
    """A pipeline holding the base corpus with every summary up to date."""
    ingest(pipeline, BASE_DOCS)
    pipeline.drain()
    return pipeline


def graph_snapshot(pipeline: Pipeline) -> dict:
    """Everything that must not change when an unrelated document is deleted."""
    return {
        "entities": {
            e.entity_id: (e.canonical_name, tuple(sorted(e.mentions)))
            for e in pipeline.store.all_entities()
        },
        "relations": {
            r.relation_id: (r.source_id, r.target_id, r.relation_type, tuple(sorted(r.provenance)))
            for r in pipeline.store.all_relations()
        },
        "chunks": {c.chunk_id for c in pipeline.store.all_chunks()},
    }
