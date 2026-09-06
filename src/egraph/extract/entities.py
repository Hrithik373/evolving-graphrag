"""Entity/relation extraction over chunks - cached, metered, and normalised."""

from __future__ import annotations

import logging

from egraph.extract.cache import ExtractionCache, cache_key
from egraph.gateway.client import LLMClient
from egraph.observability import metrics
from egraph.schemas import Chunk, ExtractedEntity, ExtractedRelation, Extraction

log = logging.getLogger(__name__)

MAX_NAME_CHARS = 120


class Extractor:
    def __init__(self, llm: LLMClient, cache: ExtractionCache) -> None:
        self.llm = llm
        self.cache = cache

    def extract_chunk(self, chunk: Chunk) -> Extraction:
        key = cache_key(chunk.text, self.llm.settings.llm_model)
        cached = self.cache.get(key)
        if cached is not None:
            metrics.EXTRACTION_CACHE.labels(result="hit").inc()
            # Still metered - a cache hit is a zero-cost call, not an absent one.
            self.llm.meter.record("extract", cache_hit=True, doc_id=chunk.doc_id)
            return _to_extraction(chunk.chunk_id, cached)
        metrics.EXTRACTION_CACHE.labels(result="miss").inc()
        payload = self.llm.extract(chunk.text, doc_id=chunk.doc_id)
        self.cache.set(key, payload)
        return _to_extraction(chunk.chunk_id, payload)

    def extract_chunks(self, chunks: list[Chunk]) -> list[Extraction]:
        return [self.extract_chunk(chunk) for chunk in chunks]


def _to_extraction(chunk_id: str, payload: dict) -> Extraction:
    entities: list[ExtractedEntity] = []
    seen: set[str] = set()
    for raw in payload.get("entities", []) or []:
        name = str(raw.get("name", "")).strip()[:MAX_NAME_CHARS]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        entities.append(
            ExtractedEntity(
                name=name,
                type=str(raw.get("type", "concept")).strip().lower() or "concept",
                description=str(raw.get("description", "")).strip(),
            )
        )
    known = {e.name.lower() for e in entities}
    relations: list[ExtractedRelation] = []
    for raw in payload.get("relations", []) or []:
        source = str(raw.get("source", "")).strip()[:MAX_NAME_CHARS]
        target = str(raw.get("target", "")).strip()[:MAX_NAME_CHARS]
        # A relation whose endpoints are not both extracted entities has no provenance
        # anchor, so it is dropped rather than silently inventing an entity.
        if not source or not target or source.lower() == target.lower():
            continue
        if source.lower() not in known or target.lower() not in known:
            log.debug("dropping relation with unextracted endpoint: %s -> %s", source, target)
            continue
        relations.append(
            ExtractedRelation(
                source=source,
                target=target,
                relation_type=str(raw.get("relation_type", "related_to")).strip().lower()
                or "related_to",
                description=str(raw.get("description", "")).strip(),
            )
        )
    return Extraction(chunk_id=chunk_id, entities=entities, relations=relations)
