"""Document intake: identity, content hashing, idempotent dedup."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from egraph.schemas import DocumentChange, SourceDocument, content_hash, utcnow
from egraph.store.base import GraphStore

SLUG = re.compile(r"[^a-z0-9]+")


def make_doc_id(uri: str) -> str:
    stem = SLUG.sub("-", Path(uri).stem.lower()).strip("-")
    if not stem:
        stem = hashlib.sha256(uri.encode()).hexdigest()[:12]
    return stem[:64]


def is_noop(store: GraphStore, change: DocumentChange) -> bool:
    """Re-ingesting unchanged content must not touch the graph or spend a token.

    This is a correctness property, not an optimisation: without it, a retried delivery
    would re-mark communities dirty and inflate the measured update cost.
    """
    if change.op == "delete":
        existing = store.get_document(change.doc_id)
        return existing is None or existing.status == "deleted"
    digest = change.resolved_hash()
    if digest is None:
        return False
    existing = store.get_document(change.doc_id)
    return existing is not None and existing.status == "active" and existing.content_hash == digest


def upsert_document_record(store: GraphStore, change: DocumentChange) -> SourceDocument:
    digest = change.resolved_hash() or ""
    existing = store.get_document(change.doc_id)
    if existing is None:
        doc = SourceDocument(
            doc_id=change.doc_id,
            uri=change.uri or change.doc_id,
            content_hash=digest,
            text=change.content or "",
        )
    else:
        doc = existing
        doc.uri = change.uri or doc.uri
        doc.content_hash = digest
        doc.text = change.content or ""
        doc.version += 1
        doc.status = "active"
        doc.updated_at = utcnow()
    store.upsert_document(doc)
    return doc


def load_corpus(directory: str | Path) -> list[DocumentChange]:
    """Read a directory of .txt/.md files as add-changes. Used by fixtures and the CLI."""
    root = Path(directory)
    changes: list[DocumentChange] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in {".txt", ".md"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        changes.append(
            DocumentChange(
                op="add",
                doc_id=make_doc_id(path.name),
                uri=str(path.as_posix()),
                content=text,
                content_hash=content_hash(text),
            )
        )
    return changes
