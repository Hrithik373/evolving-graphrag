"""Dirty tracking and selective recompute - the cost lever, verified.

The claim under test is not "recompute happens" but "recompute is *selective*": a change
confined to one region of the graph must leave the summaries of every other region alone.
"""

from __future__ import annotations

from conftest import ingest

from egraph.schemas import DocumentChange
from fixtures.mini_corpus import BASE_DOCS, NEW_DOCS, UPDATED_DOCS


def test_index_is_clean_after_a_drain(loaded):
    assert loaded.store.dirty_communities() == []
    assert loaded.staleness().dirty_count == 0
    assert loaded.staleness().dirty_fraction == 0.0


def test_a_local_update_dirties_only_some_communities(loaded):
    total = len(loaded.store.all_communities())
    assert total >= 4, "corpus must produce several communities for this to mean anything"

    loaded.apply(DocumentChange(op="update", doc_id="northwind", content=UPDATED_DOCS["northwind"]))
    dirty = loaded.store.dirty_communities()

    assert dirty, "an update that changed facts must dirty something"
    assert (
        len(dirty) < total
    ), f"update dirtied {len(dirty)}/{total} communities - that is a rebuild, not maintenance"


def test_recompute_touches_only_dirty_communities(loaded):
    clean_before = {
        c.community_id: c.summary_hash for c in loaded.store.all_communities() if not c.dirty
    }
    loaded.apply(DocumentChange(op="update", doc_id="northwind", content=UPDATED_DOCS["northwind"]))
    dirty_ids = {c.community_id for c in loaded.store.dirty_communities()}
    untouched = {cid: h for cid, h in clean_before.items() if cid not in dirty_ids}

    report = loaded.drain()

    assert report.recomputed == len(dirty_ids)
    for community_id, summary_hash in untouched.items():
        assert (
            loaded.store.get_community(community_id).summary_hash == summary_hash
        ), f"{community_id} was clean but its summary changed"


def test_recompute_respects_the_batch_size(loaded):
    ingest(loaded, NEW_DOCS)
    dirty_before = len(loaded.store.dirty_communities())
    assert dirty_before > 2

    report = loaded.recompute_dirty(batch_size=2)
    assert report.recomputed == 2
    assert report.remaining_dirty == dirty_before - 2


def test_staleness_reports_reality(loaded):
    loaded.apply(DocumentChange(op="add", doc_id="new-doc", content=NEW_DOCS["meridian-project"]))
    staleness = loaded.staleness()
    assert staleness.dirty_count == len(loaded.store.dirty_communities())
    assert staleness.community_count == len(loaded.store.all_communities())
    assert 0 < staleness.dirty_fraction <= 1.0
    assert staleness.oldest_dirty_age_s >= 0.0

    loaded.drain()
    assert loaded.staleness().dirty_count == 0


def test_queries_report_the_staleness_they_touched(loaded):
    from egraph.schemas import QueryRequest

    loaded.apply(DocumentChange(op="update", doc_id="northwind", content=UPDATED_DOCS["northwind"]))
    answer = loaded.query(
        QueryRequest(question="Who leads the platform team at Northwind Analytics?")
    )
    # The answer is still served, but the freshness cost of serving it is visible.
    assert answer.text
    assert answer.stale_communities_touched == sum(
        1 for cid in answer.used_communities if loaded.store.get_community(cid).dirty
    )

    loaded.drain()
    fresh = loaded.query(
        QueryRequest(question="Who leads the platform team at Northwind Analytics?")
    )
    assert fresh.stale_communities_touched == 0


def test_reingesting_unchanged_content_dirties_nothing(loaded):
    before = {c.community_id: c.dirty for c in loaded.store.all_communities()}
    result = loaded.apply(
        DocumentChange(op="add", doc_id="northwind", content=BASE_DOCS["northwind"])
    )
    assert result.op == "noop"
    after = {c.community_id: c.dirty for c in loaded.store.all_communities()}
    assert before == after


def test_summary_records_the_membership_it_covered(loaded):
    for community in loaded.store.all_communities():
        assert (
            community.summarized_members == community.members
        ), "a clean summary must record exactly the members it was written from"
