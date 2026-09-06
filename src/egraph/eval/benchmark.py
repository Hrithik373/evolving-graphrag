"""Time-split churn benchmark.

The corpus is split the way a real index ages rather than shuffled:

    t0  base corpus            -> build the index
    t1  updates + new docs     -> the corpus changes under the index
    t2  deletions              -> documents are retracted

Questions are asked at the right times: base questions at t0 and again at t1 (historical
stability - did keeping up with change damage what already worked?), new questions at t1
(did the change actually land?), and deletion questions at t2 (is retracted information
still being served?).

That last one is the point of the whole project, so it is measured twice over: the
must-exclude facts detect *under*-deletion, and a separate surviving-facts set detects
*over*-deletion. A system that deletes its whole index scores perfectly on the first and
fails the second.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from egraph.schemas import DocumentChange
from fixtures.mini_corpus import (
    BASE_DOCS,
    DELETION_SET,
    NEW_DOCS,
    QUESTIONS,
    SURVIVING_FACTS,
    UPDATED_DOCS,
    Question,
)


@dataclass
class ChurnBenchmark:
    """A time-split corpus plus the question sets for each phase."""

    base: dict[str, str] = field(default_factory=dict)
    updates: dict[str, str] = field(default_factory=dict)
    new: dict[str, str] = field(default_factory=dict)
    deletions: tuple[str, ...] = ()
    base_questions: list[Question] = field(default_factory=list)
    new_questions: list[Question] = field(default_factory=list)
    deletion_questions: list[Question] = field(default_factory=list)
    surviving_questions: list[Question] = field(default_factory=list)

    # ------------------------------------------------------------------ change streams
    def base_changes(self) -> list[DocumentChange]:
        return [
            DocumentChange(op="add", doc_id=doc_id, uri=f"{doc_id}.md", content=text)
            for doc_id, text in self.base.items()
        ]

    def churn_changes(self) -> list[DocumentChange]:
        """t1: the corpus moves. Updates first, then arrivals - the order a feed delivers."""
        changes = [
            DocumentChange(op="update", doc_id=doc_id, uri=f"{doc_id}.md", content=text)
            for doc_id, text in self.updates.items()
        ]
        changes += [
            DocumentChange(op="add", doc_id=doc_id, uri=f"{doc_id}.md", content=text)
            for doc_id, text in self.new.items()
        ]
        return changes

    def deletion_changes(self) -> list[DocumentChange]:
        return [DocumentChange(op="delete", doc_id=doc_id) for doc_id in self.deletions]

    @property
    def size(self) -> dict[str, int]:
        return {
            "base_docs": len(self.base),
            "updated_docs": len(self.updates),
            "new_docs": len(self.new),
            "deleted_docs": len(self.deletions),
            "base_questions": len(self.base_questions),
            "new_questions": len(self.new_questions),
            "deletion_questions": len(self.deletion_questions),
            "surviving_questions": len(self.surviving_questions),
        }


def build_benchmark() -> ChurnBenchmark:
    """The bundled offline benchmark. Deterministic, no downloads, runs in seconds."""
    return ChurnBenchmark(
        base=dict(BASE_DOCS),
        updates=dict(UPDATED_DOCS),
        new=dict(NEW_DOCS),
        deletions=DELETION_SET,
        base_questions=list(QUESTIONS.base),
        new_questions=list(QUESTIONS.new),
        deletion_questions=list(QUESTIONS.deletion),
        surviving_questions=list(SURVIVING_FACTS),
    )


def build_scaled_benchmark(copies: int) -> ChurnBenchmark:
    """Replicate the corpus ``copies`` times with disjoint entity names.

    Used for the memory-growth-vs-corpus-size figure and to check that update cost stays
    flat as the corpus grows - the property that separates incremental maintenance from
    reindexing. Questions stay pinned to copy 0 so they remain answerable.
    """
    benchmark = build_benchmark()
    if copies <= 1:
        return benchmark

    # The suffix has to read as a proper noun itself. A digit suffix ("Helios Labs 2")
    # is invisible to a capitalised-phrase extractor, so every copy would collapse back
    # onto the same entities and the corpus would not actually grow.
    tags = ["", "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota"]

    def relabel(text: str, index: int) -> str:
        tag = tags[index] if index < len(tags) else f"Unit{index}"
        for name in (
            "Helios Labs",
            "Aurora Engine",
            "Northwind Analytics",
            "Kestrel Systems",
            "Fjord Protocol",
            "Sable Protocol",
            "Tundra Protocol",
            "Vantage Robotics",
            "Marit Solberg",
            "Pieter Vos",
            "Ines Duarte",
            "Nuria Castell",
            "Sofia Almeida",
            "Meridian",
            "Lumen",
            "Tideline",
            "Harrier",
            "Trondheim",
            "Rotterdam",
            "Porto",
            "Bilbao",
            "Lisbon",
        ):
            text = text.replace(name, f"{name} {tag}")
        return text

    for index in range(1, copies):
        for doc_id, text in BASE_DOCS.items():
            benchmark.base[f"{doc_id}-{index}"] = relabel(text, index)
        for doc_id, text in UPDATED_DOCS.items():
            benchmark.updates[f"{doc_id}-{index}"] = relabel(text, index)
        for doc_id, text in NEW_DOCS.items():
            benchmark.new[f"{doc_id}-{index}"] = relabel(text, index)
    return benchmark
