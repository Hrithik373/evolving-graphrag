"""Community summarisation: turn a community's subgraph into a summary the global
retrieval path can read."""

from __future__ import annotations

import logging

from egraph.gateway.client import LLMClient
from egraph.gateway.embeddings import Embedder
from egraph.schemas import Community, utcnow
from egraph.store import communities as community_ops
from egraph.store.base import GraphStore

log = logging.getLogger(__name__)


def community_context(
    store: GraphStore, community: Community, max_entities: int = 20
) -> tuple[list[str], list[str]]:
    """Describe a community as two lists of lines: its entities and its internal relations.

    Entities are ranked by degree so a truncated context keeps the hubs - the members that
    actually define what the community is about.
    """
    entities = [e for e in (store.get_entity(m) for m in community.members) if e is not None]
    entities.sort(key=lambda e: (-e.degree, e.canonical_name))
    kept = entities[:max_entities]
    kept_ids = {e.entity_id for e in kept}

    entity_lines = [
        f"{e.canonical_name} ({e.type})" + (f": {e.description[:160]}" if e.description else "")
        for e in kept
    ]

    names = {e.entity_id: e.canonical_name for e in kept}
    relation_lines: list[str] = []
    seen: set[str] = set()
    for entity in kept:
        for relation in store.relations_for_entity(entity.entity_id):
            if relation.relation_id in seen:
                continue
            if relation.source_id not in kept_ids or relation.target_id not in kept_ids:
                continue
            seen.add(relation.relation_id)
            relation_lines.append(
                f"{names[relation.source_id]} --{relation.relation_type}--> "
                f"{names[relation.target_id]}"
            )
    return entity_lines, relation_lines


class Summarizer:
    def __init__(
        self,
        store: GraphStore,
        llm: LLMClient,
        embedder: Embedder,
        max_entities: int = 20,
    ) -> None:
        self.store = store
        self.llm = llm
        self.embedder = embedder
        self.max_entities = max_entities

    def summarise(self, community: Community) -> Community:
        """Regenerate one community's summary and clear its dirty flag.

        The summary records ``summarized_members``: the exact entity set it covered. That
        is what makes drift detectable - if membership later differs from this set, the
        summary is known to be stale rather than merely suspected of it.
        """
        entity_lines, relation_lines = community_context(self.store, community, self.max_entities)
        payload = self.llm.summarise(
            entity_lines, relation_lines, community_id=community.community_id
        )
        community.title = payload["title"] or community.title
        community.summary = payload["summary"]
        community.summary_hash = community_ops.summary_hash(community.title, community.summary)
        community.embedding = self.embedder.embed(f"{community.title}\n{community.summary}")
        community.summarized_members = set(community.members)
        community.member_count = len(community.members)
        community.dirty = False
        community.dirty_since = None
        community.updated_at = utcnow()
        self.store.upsert_community(community)
        return community
