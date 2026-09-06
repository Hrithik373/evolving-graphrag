"""Chunking.

The chunker has one job beyond splitting text: produce **content-addressed chunk ids** so
that re-chunking an edited document yields identical ids for the paragraphs that did not
change. That is what lets ``update_document`` reuse extractions and touch only the delta -
the single biggest cost lever in the system.

Chunk boundaries therefore snap to paragraph/sentence breaks rather than to a rolling
character offset, so inserting a paragraph shifts one chunk, not every chunk after it.
"""

from __future__ import annotations

import re

from egraph.schemas import Chunk, content_hash

PARAGRAPH = re.compile(r"\n\s*\n")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Pack paragraphs into chunks up to ``chunk_size`` characters."""
    text = text.strip()
    if not text:
        return []
    units = [p.strip() for p in PARAGRAPH.split(text) if p.strip()]
    # A single giant paragraph still has to be broken up; do it on sentence boundaries.
    expanded: list[str] = []
    for unit in units:
        if len(unit) <= chunk_size:
            expanded.append(unit)
            continue
        buffer = ""
        for sentence in SENTENCE.split(unit):
            if buffer and len(buffer) + len(sentence) + 1 > chunk_size:
                expanded.append(buffer.strip())
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}".strip()
        if buffer.strip():
            expanded.append(buffer.strip())

    chunks: list[str] = []
    buffer = ""
    for unit in expanded:
        if buffer and len(buffer) + len(unit) + 2 > chunk_size:
            chunks.append(buffer)
            # Carry a sentence of context so a fact split across a boundary survives.
            tail = buffer[-overlap:] if overlap else ""
            if tail:
                cut = tail.find(" ")
                tail = tail[cut + 1 :] if cut != -1 else ""
            buffer = f"{tail} {unit}".strip() if tail else unit
        else:
            buffer = f"{buffer}\n\n{unit}".strip() if buffer else unit
    if buffer:
        chunks.append(buffer)
    return chunks


def chunk_document(
    doc_id: str, text: str, chunk_size: int = 700, overlap: int = 100
) -> list[Chunk]:
    pieces = split_text(text, chunk_size, overlap)
    seen: dict[str, int] = {}
    chunks: list[Chunk] = []
    for position, piece in enumerate(pieces):
        digest = content_hash(piece)
        occurrence = seen.get(digest, 0)
        seen[digest] = occurrence + 1
        chunks.append(
            Chunk(
                chunk_id=Chunk.make_id(doc_id, piece, occurrence),
                doc_id=doc_id,
                text=piece,
                position=position,
                content_hash=digest,
            )
        )
    return chunks
