"""Graph-side contracts. Provenance lives on Relation and is the retraction lever."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from egraph.schemas.document import utcnow


def entity_key(name: str, etype: str) -> str:
    """Canonical key for an entity. Resolution may still merge two different keys."""
    norm = " ".join(name.strip().lower().split())
    return hashlib.sha256(f"{norm}|{etype.strip().lower()}".encode()).hexdigest()[:24]


def relation_key(source_id: str, target_id: str, relation_type: str) -> str:
    return hashlib.sha256(
        f"{source_id}|{target_id}|{relation_type.strip().lower()}".encode()
    ).hexdigest()[:24]


class Entity(BaseModel):
    entity_id: str
    canonical_name: str
    type: str = "concept"
    # Derived state, and therefore provenance-tracked. `description` is whatever
    # `descriptions` currently supports; keeping only the flat string would let an entity
    # that survives a deletion keep prose copied out of the deleted document.
    description: str = ""
    descriptions: dict[str, str] = Field(default_factory=dict)
    embedding: list[float] = Field(default_factory=list)
    degree: int = 0
    community_id: str | None = None
    # Chunks that mention this entity. Empty set => orphan => collected.
    mentions: set[str] = Field(default_factory=set)
    aliases: set[str] = Field(default_factory=set)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    model_config = {"arbitrary_types_allowed": True}


class Relation(BaseModel):
    """An edge whose existence is exactly the union of the chunks that support it."""

    relation_id: str
    source_id: str
    target_id: str
    relation_type: str = "related_to"
    description: str = ""
    descriptions: dict[str, str] = Field(default_factory=dict)
    weight: float = 1.0
    # The retraction lever. Empty provenance => the relation is garbage.
    provenance: set[str] = Field(default_factory=set)
    updated_at: datetime = Field(default_factory=utcnow)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def support(self) -> int:
        return len(self.provenance)


class Community(BaseModel):
    community_id: str
    level: int = 0
    title: str = ""
    summary: str = ""
    summary_hash: str = ""
    dirty: bool = True
    members: set[str] = Field(default_factory=set)
    member_count: int = 0
    # Entity set the standing summary actually covered - lets us detect drift.
    summarized_members: set[str] = Field(default_factory=set)
    embedding: list[float] = Field(default_factory=list)
    dirty_since: datetime | None = None
    # Ordering key for the recompute queue. `dirty_since` looks like it should serve, but
    # two communities marked in the same clock tick get identical timestamps, and whether
    # they tie depends on wall-clock granularity - so oldest-first silently alternated
    # between time order and insertion order between runs. A counter is a total order by
    # construction, which is what a reproducible eval needs.
    dirty_seq: int = 0
    updated_at: datetime = Field(default_factory=utcnow)

    model_config = {"arbitrary_types_allowed": True}


class ResolutionDecision(BaseModel):
    """Logged for every candidate entity so resolution quality is auditable in eval."""

    candidate_name: str
    candidate_type: str
    decision: Literal["merge", "create"]
    matched_entity_id: str | None = None
    similarity: float = 0.0
    name_match: bool = False
    reason: str = ""
    chunk_id: str | None = None
    at: datetime = Field(default_factory=utcnow)


class ExtractedEntity(BaseModel):
    """Raw LLM output, before resolution."""

    name: str
    type: str = "concept"
    description: str = ""


class ExtractedRelation(BaseModel):
    source: str
    target: str
    relation_type: str = "related_to"
    description: str = ""
    weight: float = 1.0


class Extraction(BaseModel):
    """The result of extracting one chunk. Cached by chunk content hash."""

    chunk_id: str
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
