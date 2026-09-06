"""Ingestion: intake, content-addressed chunking, entity resolution."""

from egraph.ingest.chunker import chunk_document, split_text
from egraph.ingest.loader import is_noop, load_corpus, make_doc_id, upsert_document_record
from egraph.ingest.resolver import EntityResolver, name_similarity, normalise_name

__all__ = [
    "EntityResolver",
    "chunk_document",
    "is_noop",
    "load_corpus",
    "make_doc_id",
    "name_similarity",
    "normalise_name",
    "split_text",
    "upsert_document_record",
]
