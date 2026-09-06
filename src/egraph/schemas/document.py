"""Document-side contracts: what enters the system and how it is split."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def content_hash(text: str) -> str:
    """Stable content hash. Idempotent ingest depends on this being pure."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def utcnow() -> datetime:
    return datetime.now(UTC)


DocumentStatus = Literal["active", "deleted"]


class SourceDocument(BaseModel):
    """A document as the index knows it. Deletion is a status change plus provenance GC."""

    doc_id: str
    uri: str
    content_hash: str
    version: int = 1
    status: DocumentStatus = "active"
    ingested_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    # Kept so update_document can diff without re-fetching the original source.
    text: str = ""


class Chunk(BaseModel):
    """A unit of provenance. Every entity and relation traces back to a set of these."""

    chunk_id: str
    doc_id: str
    text: str
    position: int
    content_hash: str
    embedding: list[float] = Field(default_factory=list)

    @staticmethod
    def make_id(doc_id: str, text: str, occurrence: int = 0) -> str:
        """Chunk ids are content-addressed and deliberately **position-independent**.

        An unchanged paragraph keeps its chunk id even when a paragraph is inserted above
        it, so ``update_document`` reuses its extraction and its provenance instead of
        re-extracting the whole document. ``occurrence`` disambiguates a document that
        repeats the same text verbatim.
        """
        suffix = f"::{occurrence}" if occurrence else ""
        return f"{doc_id}::{content_hash(text)[:16]}{suffix}"


class DocumentChange(BaseModel):
    """The only way to mutate the index."""

    op: Literal["add", "update", "delete"]
    doc_id: str
    uri: str | None = None
    content: str | None = None  # None for delete
    content_hash: str | None = None

    def resolved_hash(self) -> str | None:
        if self.content_hash:
            return self.content_hash
        if self.content is None:
            return None
        return content_hash(self.content)
