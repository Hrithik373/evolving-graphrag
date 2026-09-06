"""Update diffing: what actually changed between two versions of a document.

Two levels:

* **Chunk level** (``diff_chunks``) - because chunk ids are content-addressed, the diff is
  a set difference. Unchanged paragraphs keep their ids, so they are neither re-extracted
  nor re-embedded, and their provenance is left exactly where it is. The cost of an update
  scales with the size of the edit, not the size of the document.
* **Extraction level** (``diff_extractions``) - for the chunks that did change, compare the
  old and new facts so we emit only the delta rather than dropping and re-adding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from egraph.schemas import Chunk, Extraction, entity_key, relation_key


@dataclass
class ChunkDiff:
    added: list[Chunk] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    # Same text, new position: reuse everything, just fix the ordering field.
    moved: list[Chunk] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(self.added or self.removed)

    def summary(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "kept": len(self.kept),
            "moved": len(self.moved),
        }


def diff_chunks(old: list[Chunk], new: list[Chunk]) -> ChunkDiff:
    old_by_id = {chunk.chunk_id: chunk for chunk in old}
    new_by_id = {chunk.chunk_id: chunk for chunk in new}
    diff = ChunkDiff()
    for chunk_id, chunk in new_by_id.items():
        previous = old_by_id.get(chunk_id)
        if previous is None:
            diff.added.append(chunk)
        else:
            diff.kept.append(chunk_id)
            if previous.position != chunk.position:
                diff.moved.append(chunk)
    diff.removed = [chunk_id for chunk_id in old_by_id if chunk_id not in new_by_id]
    return diff


@dataclass
class ExtractionDiff:
    entities_added: list[tuple[str, str, str]] = field(default_factory=list)  # (id, name, type)
    entities_removed: list[str] = field(default_factory=list)
    relations_added: list[str] = field(default_factory=list)
    relations_removed: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.entities_added
            or self.entities_removed
            or self.relations_added
            or self.relations_removed
        )


def _entity_ids(extraction: Extraction) -> dict[str, tuple[str, str]]:
    return {
        entity_key(entity.name, entity.type): (entity.name, entity.type)
        for entity in extraction.entities
    }


def _relation_ids(extraction: Extraction) -> set[str]:
    ids: set[str] = set()
    lookup = {entity.name.lower(): entity for entity in extraction.entities}
    for relation in extraction.relations:
        source = lookup.get(relation.source.lower())
        target = lookup.get(relation.target.lower())
        if source is None or target is None:
            continue
        ids.add(
            relation_key(
                entity_key(source.name, source.type),
                entity_key(target.name, target.type),
                relation.relation_type,
            )
        )
    return ids


def diff_extractions(old: Extraction | None, new: Extraction) -> ExtractionDiff:
    """Delta between the facts a chunk used to assert and the ones it asserts now."""
    old_entities = _entity_ids(old) if old else {}
    new_entities = _entity_ids(new)
    old_relations = _relation_ids(old) if old else set()
    new_relations = _relation_ids(new)
    return ExtractionDiff(
        entities_added=[
            (entity_id, name, etype)
            for entity_id, (name, etype) in new_entities.items()
            if entity_id not in old_entities
        ],
        entities_removed=[eid for eid in old_entities if eid not in new_entities],
        relations_added=sorted(new_relations - old_relations),
        relations_removed=sorted(old_relations - new_relations),
    )
