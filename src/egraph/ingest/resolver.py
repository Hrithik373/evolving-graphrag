"""Entity resolution.

GraphRAG's well-known weak point, so the decision is explicit, thresholded, and logged:
every candidate produces a :class:`ResolutionDecision` row, which makes resolution quality
auditable and lets the eval sweep ``tau_sim`` instead of hand-waving about it.

Merge rule (both must hold):
    cosine(candidate, existing) >= tau_sim   AND   normalised-name similarity >= tau_name

Requiring both is deliberate. Embedding similarity alone merges "Anthropic" with
"OpenAI" (same shape of thing, near-identical contexts); name similarity alone merges
"Apple Inc" the company with "Apple" the fruit across a corpus. Under churn a bad merge is
worse than a duplicate: merging two entities fuses their provenance sets, and a later
deletion can then no longer separate what each document supported.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Literal

from egraph.schemas import Entity, ResolutionDecision, entity_key, utcnow
from egraph.settings import Settings, get_settings
from egraph.store.base import GraphStore
from egraph.store.vectors import cosine

log = logging.getLogger(__name__)

PUNCT = re.compile(r"[^\w\s]")
LEGAL_SUFFIX = re.compile(
    r"\b(inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|gmbh|sa|nv|ag|the)\b"
)


def normalise_name(name: str) -> str:
    text = PUNCT.sub(" ", name.lower())
    text = LEGAL_SUFFIX.sub(" ", text)
    return " ".join(text.split())


def name_similarity(a: str, b: str) -> float:
    left, right = normalise_name(a), normalise_name(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    # An acronym matching the initials of a multi-word name is a strong signal.
    if left.replace(" ", "") == "".join(w[0] for w in right.split()):
        return 0.95
    if right.replace(" ", "") == "".join(w[0] for w in left.split()):
        return 0.95
    return SequenceMatcher(None, left, right).ratio()


class EntityResolver:
    def __init__(self, store: GraphStore, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or get_settings()

    def resolve(
        self, candidate: Entity, chunk_id: str | None = None
    ) -> tuple[str, Literal["merge", "create"]]:
        """Return the entity id this candidate belongs to, and how that was decided."""
        exact = self.store.get_entity(entity_key(candidate.canonical_name, candidate.type))
        if exact is not None:
            self._log(candidate, "merge", exact, 1.0, True, "exact canonical key", chunk_id)
            return exact.entity_id, "merge"

        best: Entity | None = None
        best_sim = 0.0
        best_name = 0.0
        if candidate.embedding:
            for existing, similarity in self.store.search_entities(
                candidate.embedding, self.settings.resolver_candidates
            ):
                if existing.entity_id == candidate.entity_id:
                    continue
                names = [existing.canonical_name, *existing.aliases]
                name_score = max(name_similarity(candidate.canonical_name, n) for n in names)
                clears_both = (
                    similarity >= self.settings.tau_sim and name_score >= self.settings.tau_name
                )
                if clears_both and similarity + name_score > best_sim + best_name:
                    best, best_sim, best_name = existing, similarity, name_score

        if best is not None:
            self._log(
                candidate,
                "merge",
                best,
                best_sim,
                True,
                f"cosine={best_sim:.3f}>=tau_sim and name={best_name:.3f}>=tau_name",
                chunk_id,
            )
            return best.entity_id, "merge"

        self._log(
            candidate,
            "create",
            None,
            best_sim,
            False,
            "no candidate cleared both thresholds",
            chunk_id,
        )
        return candidate.entity_id, "create"

    def candidate_entity(
        self, name: str, etype: str, description: str, embedding: list[float]
    ) -> Entity:
        return Entity(
            entity_id=entity_key(name, etype),
            canonical_name=name,
            type=etype,
            description=description,
            embedding=embedding,
            created_at=utcnow(),
            updated_at=utcnow(),
        )

    def _log(
        self,
        candidate: Entity,
        decision: Literal["merge", "create"],
        matched: Entity | None,
        similarity: float,
        name_match: bool,
        reason: str,
        chunk_id: str | None,
    ) -> None:
        self.store.log_resolution(
            ResolutionDecision(
                candidate_name=candidate.canonical_name,
                candidate_type=candidate.type,
                decision=decision,
                matched_entity_id=matched.entity_id if matched else None,
                similarity=round(float(similarity), 4),
                name_match=name_match,
                reason=reason,
                chunk_id=chunk_id,
            )
        )


def similarity_of(a: Entity, b: Entity) -> float:
    """Exposed for the threshold sweep in the eval harness."""
    return cosine(a.embedding, b.embedding)
