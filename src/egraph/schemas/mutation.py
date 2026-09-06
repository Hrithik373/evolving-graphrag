"""Mutation contracts. Everything the churn engine does is one of five graph mutations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from egraph.schemas.document import utcnow

MutationKind = Literal[
    "add_entity",
    "remove_entity",
    "add_relation",
    "expire_relation",
    "update_entity",
]


class GraphMutation(BaseModel):
    kind: MutationKind
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: list[str] = Field(default_factory=list)  # chunk ids
    affected_communities: list[str] = Field(default_factory=list)
    at: datetime = Field(default_factory=utcnow)


class DirtyMark(BaseModel):
    community_id: str
    reason: str
    at: datetime = Field(default_factory=utcnow)


class GCSummary(BaseModel):
    """What deletion actually removed. The deletion-correctness receipt."""

    relations_expired: int = 0
    relations_weakened: int = 0
    entities_removed: int = 0
    chunks_removed: int = 0
    communities_marked_dirty: int = 0
    removed_entity_ids: list[str] = Field(default_factory=list)
    expired_relation_ids: list[str] = Field(default_factory=list)


class Staleness(BaseModel):
    dirty_count: int = 0
    community_count: int = 0
    dirty_fraction: float = 0.0
    oldest_dirty_age_s: float = 0.0
    queue_depth: int = 0


class ChurnResult(BaseModel):
    """Returned synchronously by every write. Heavy work is enqueued, not done here."""

    doc_id: str
    op: Literal["add", "update", "delete", "noop"]
    status: str = "queued"
    mutations: list[GraphMutation] = Field(default_factory=list)
    dirty_communities: list[str] = Field(default_factory=list)
    gc_summary: GCSummary | None = None
    chunks_added: int = 0
    chunks_reused: int = 0
    chunks_removed: int = 0
    jobs_enqueued: list[str] = Field(default_factory=list)
    sync_wall_ms: float = 0.0

    @property
    def mutation_count(self) -> int:
        return len(self.mutations)
