"""Compaction: global re-clustering that does not behave like a full reindex."""

from __future__ import annotations

from conftest import ingest

from egraph.churn.compact import compact
from egraph.cluster.leiden import active_backend, cluster, label_propagation
from egraph.schemas import DocumentChange
from fixtures.mini_corpus import NEW_DOCS


def test_label_propagation_is_deterministic():
    edges = [(0, 1, 1.0), (1, 2, 1.0), (3, 4, 1.0)]
    nodes = ["a", "b", "c", "d", "e"]
    assert label_propagation(nodes, edges, seed=7) == label_propagation(nodes, edges, seed=7)


def test_clustering_separates_disconnected_regions(loaded):
    membership = cluster(loaded.store, backend=loaded.settings.cluster_backend, seed=1337)
    assert membership
    assert len(set(membership.values())) > 1, "the corpus has separable regions"


def test_compaction_is_stable_when_nothing_changed(loaded):
    first = compact(loaded.store, loaded.settings)
    loaded.drain()
    second = compact(loaded.store, loaded.settings)

    assert second.entities_moved == 0, "a second compaction moved entities with no input change"
    assert (
        second.communities_marked_dirty == 0
    ), "a no-op compaction dirtied summaries - that is a rebuild in disguise"
    assert first.communities_after == second.communities_after


def test_compaction_preserves_clean_summaries_it_did_not_disturb(loaded):
    before = {c.community_id: (c.summary_hash, c.dirty) for c in loaded.store.all_communities()}
    report = compact(loaded.store, loaded.settings)

    unchanged = 0
    for community in loaded.store.all_communities():
        previous = before.get(community.community_id)
        if previous is None or community.dirty:
            continue
        assert community.summary_hash == previous[0]
        unchanged += 1
    assert unchanged or report.communities_marked_dirty, "compaction did nothing at all"


def test_compaction_absorbs_incremental_drift(loaded):
    """The point of compaction: placement drifts, compaction repairs it."""
    ingest(loaded, NEW_DOCS)
    loaded.drain()
    report = compact(loaded.store, loaded.settings)

    assert report.communities_after > 0
    # Every entity ends up in exactly one community, and that community claims it back.
    for entity in loaded.store.all_entities():
        assert entity.community_id
        community = loaded.store.get_community(entity.community_id)
        assert community is not None
        assert entity.entity_id in community.members


def test_compaction_leaves_no_empty_communities(loaded):
    for doc_id in ("northwind", "kestrel-systems"):
        loaded.apply(DocumentChange(op="delete", doc_id=doc_id))
    compact(loaded.store, loaded.settings)
    for community in loaded.store.all_communities():
        assert community.members, f"{community.community_id} survived with no members"


def test_backend_reports_what_it_actually_used(settings):
    assert active_backend(settings.cluster_backend) in {"leiden", "louvain", "label-propagation"}


def test_compaction_on_an_empty_index_is_safe(pipeline):
    report = compact(pipeline.store, pipeline.settings)
    assert report.communities_after == 0
    assert report.entities_moved == 0


def test_louvain_fallback_does_not_collapse_the_graph(loaded):
    """CI and slim containers may have no leidenalg; the fallback must still cluster."""
    from egraph.cluster.leiden import _graph
    from egraph.cluster.louvain import louvain

    entities, edges = _graph(loaded.store)
    labels = louvain(len(entities), edges, resolution=1.0)
    assert len(set(labels)) > 1, "the Louvain fallback collapsed every entity into one community"
    assert louvain(len(entities), edges) == labels, "Louvain must be deterministic"


def test_label_propagation_is_available_for_the_ablation(loaded):
    membership = cluster(loaded.store, backend="label-propagation", seed=1337)
    assert len(membership) == len(loaded.store.all_entities())
