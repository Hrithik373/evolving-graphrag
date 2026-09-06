"""Query-side contracts. Answers carry provenance and a freshness signal, always."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from egraph.schemas.document import utcnow

QueryMode = Literal["local", "global", "dual"]


class QueryRequest(BaseModel):
    question: str
    mode: QueryMode = "dual"
    top_k_entities: int = 8
    top_k_communities: int = 3
    max_hops: int = 1


class RetrievedEntity(BaseModel):
    entity_id: str
    canonical_name: str
    type: str
    description: str
    score: float
    community_id: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    neighbours: list[str] = Field(default_factory=list)


class RetrievedCommunity(BaseModel):
    community_id: str
    title: str
    summary: str
    score: float
    dirty: bool = False
    member_count: int = 0


class RetrievalResult(BaseModel):
    entities: list[RetrievedEntity] = Field(default_factory=list)
    communities: list[RetrievedCommunity] = Field(default_factory=list)
    chunks: list[str] = Field(default_factory=list)
    context: str = ""
    mode: QueryMode = "dual"


class Answer(BaseModel):
    text: str
    citations: list[str] = Field(default_factory=list)  # source doc / chunk ids
    used_communities: list[str] = Field(default_factory=list)
    used_entities: list[str] = Field(default_factory=list)
    stale_communities_touched: int = 0
    mode: QueryMode = "dual"
    latency_ms: float = 0.0
    tokens: int = 0


class IndexStats(BaseModel):
    documents: int = 0
    active_documents: int = 0
    chunks: int = 0
    entities: int = 0
    relations: int = 0
    communities: int = 0
    dirty_communities: int = 0
    total_tokens: int = 0
    total_usd: float = 0.0


class CostRecord(BaseModel):
    """No LLM/embedding call exists unless it produced one of these."""

    operation: str
    tokens_in: int = 0
    tokens_out: int = 0
    wall_ms: float = 0.0
    usd: float = 0.0
    model: str = ""
    cache_hit: bool = False
    doc_id: str | None = None
    community_id: str | None = None
    at: datetime = Field(default_factory=utcnow)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


class ScenarioResult(BaseModel):
    """One row of the eval table."""

    system: str
    scenario: str
    n_questions: int = 0
    f1: float = 0.0
    em: float = 0.0
    stale_answer_rate: float = 0.0
    recall: float = 0.0
    update_tokens: int = 0
    update_wall_ms: float = 0.0
    communities_recomputed: int = 0
    entities: int = 0
    relations: int = 0
    notes: str = ""
