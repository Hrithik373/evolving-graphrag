"""Content-addressed chunking - the mechanism that makes an update cheap."""

from __future__ import annotations

from egraph.ingest.chunker import chunk_document, split_text
from egraph.schemas import DocumentChange
from fixtures.mini_corpus import BASE_DOCS, UPDATED_DOCS

DOC = BASE_DOCS["northwind"]


def test_chunking_is_deterministic():
    assert [c.chunk_id for c in chunk_document("d", DOC, 400, 0)] == [
        c.chunk_id for c in chunk_document("d", DOC, 400, 0)
    ]


def test_chunk_ids_are_position_independent():
    """Inserting a paragraph at the top must not renumber every chunk below it.

    If chunk ids embedded their position, a one-line edit at the top of a document would
    invalidate the whole document - and update cost would scale with document size.
    """
    original = chunk_document("d", DOC, 400, 0)
    shifted = chunk_document("d", "A brand new opening paragraph.\n\n" + DOC, 400, 0)
    original_ids = {c.chunk_id for c in original}
    shifted_ids = {c.chunk_id for c in shifted}
    reused = original_ids & shifted_ids
    assert reused, "no chunk survived an insertion at the top of the document"
    assert len(reused) >= len(original_ids) - 1


def test_a_local_edit_reuses_most_chunks(pipeline):
    pipeline.apply(DocumentChange(op="add", doc_id="northwind", uri="n.md", content=DOC))
    result = pipeline.apply(
        DocumentChange(op="update", doc_id="northwind", content=UPDATED_DOCS["northwind"])
    )
    assert (
        result.chunks_reused >= result.chunks_added
    ), "a one-paragraph edit re-extracted more than it reused"


def test_a_rewrite_reuses_almost_nothing(pipeline):
    pipeline.apply(
        DocumentChange(
            op="add", doc_id="kestrel-systems", uri="k.md", content=BASE_DOCS["kestrel-systems"]
        )
    )
    result = pipeline.apply(
        DocumentChange(
            op="update", doc_id="kestrel-systems", content=UPDATED_DOCS["kestrel-systems"]
        )
    )
    # The honest half: when the document really changed, the update really costs.
    assert result.chunks_added > 0
    assert result.chunks_reused <= result.chunks_added


def test_reingesting_identical_content_is_a_noop(pipeline):
    pipeline.apply(DocumentChange(op="add", doc_id="northwind", uri="n.md", content=DOC))
    tokens_before = pipeline.meter.snapshot()["tokens"]

    result = pipeline.apply(DocumentChange(op="add", doc_id="northwind", uri="n.md", content=DOC))

    assert result.op == "noop"
    assert result.mutations == []
    assert pipeline.meter.snapshot()["tokens"] == tokens_before, "a no-op re-ingest spent tokens"


def test_unchanged_chunks_are_not_re_extracted(pipeline):
    pipeline.apply(DocumentChange(op="add", doc_id="northwind", uri="n.md", content=DOC))
    calls_before = pipeline.meter.tally.by_operation["extract"]

    pipeline.apply(
        DocumentChange(op="update", doc_id="northwind", content=UPDATED_DOCS["northwind"])
    )
    extraction_cost = pipeline.meter.tally.by_operation["extract"] - calls_before

    # Cost of the edit must be strictly less than re-extracting the whole document.
    assert 0 < extraction_cost < calls_before


def test_split_text_respects_the_size_budget():
    text = "\n\n".join(f"Paragraph number {i} with some filler text." * 3 for i in range(20))
    chunks = split_text(text, 400, 0)
    assert chunks
    # A single oversized paragraph may exceed the budget slightly; nothing should be wild.
    assert all(len(chunk) <= 800 for chunk in chunks)


def test_empty_document_produces_no_chunks():
    assert chunk_document("d", "   \n\n  ", 400, 0) == []
