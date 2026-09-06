"""Entity resolution: the merge decision, and the audit trail behind it."""

from __future__ import annotations

from egraph.ingest.resolver import EntityResolver, name_similarity, normalise_name
from egraph.schemas import Entity, entity_key


def _candidate(resolver, name, etype="organization", description=""):
    from egraph.gateway.embeddings import hash_embed

    return resolver.candidate_entity(
        name, etype, description, hash_embed(f"{name}. {description}", 256)
    )


def test_normalise_strips_legal_suffixes_and_punctuation():
    assert normalise_name("Helios Labs, Inc.") == "helios labs"
    assert normalise_name("  The   HELIOS   Labs ") == "helios labs"


def test_name_similarity_handles_acronyms():
    assert name_similarity("Helios Labs", "Helios Labs") == 1.0
    assert name_similarity("HL", "Helios Labs") >= 0.9
    assert name_similarity("Helios Labs", "Northwind Analytics") < 0.6


def test_identical_names_merge(pipeline):
    resolver = EntityResolver(pipeline.store, pipeline.settings)
    first = _candidate(resolver, "Helios Labs")
    pipeline.store.upsert_entity(first)

    entity_id, decision = resolver.resolve(_candidate(resolver, "Helios Labs"))
    assert decision == "merge"
    assert entity_id == first.entity_id


def test_different_entities_do_not_merge(pipeline):
    resolver = EntityResolver(pipeline.store, pipeline.settings)
    pipeline.store.upsert_entity(_candidate(resolver, "Helios Labs"))

    entity_id, decision = resolver.resolve(_candidate(resolver, "Northwind Analytics"))
    assert decision == "create"
    assert entity_id == entity_key("Northwind Analytics", "organization")


def test_both_thresholds_must_clear(pipeline):
    """Embedding similarity alone must not merge two different organizations.

    Under churn a bad merge is worse than a duplicate: it fuses two provenance sets, and a
    later deletion can no longer tell which document supported which half.
    """
    resolver = EntityResolver(pipeline.store, pipeline.settings)
    a = _candidate(resolver, "Helios Labs", description="A systems research organization.")
    pipeline.store.upsert_entity(a)
    # Near-identical description, different name.
    b = _candidate(resolver, "Aurora Holdings", description="A systems research organization.")
    _, decision = resolver.resolve(b)
    assert decision == "create"


def test_every_decision_is_logged(pipeline):
    resolver = EntityResolver(pipeline.store, pipeline.settings)
    pipeline.store.upsert_entity(_candidate(resolver, "Helios Labs"))
    resolver.resolve(_candidate(resolver, "Helios Labs"), chunk_id="chunk-1")
    resolver.resolve(_candidate(resolver, "Northwind Analytics"), chunk_id="chunk-1")

    decisions = pipeline.store.list_resolutions()
    assert len(decisions) == 2
    assert {d.decision for d in decisions} == {"merge", "create"}
    assert all(d.reason for d in decisions), "every decision must record why"
    assert all(d.chunk_id == "chunk-1" for d in decisions)


def test_resolution_is_auditable_after_a_real_ingest(loaded):
    decisions = loaded.store.list_resolutions(limit=1000)
    assert decisions, "ingest must leave a resolution trail"
    merges = [d for d in decisions if d.decision == "merge"]
    assert merges, "a corpus that repeats entity names must produce merges"
    for decision in merges:
        assert decision.matched_entity_id
        assert loaded.store.get_entity(decision.matched_entity_id) is not None


def test_threshold_sweep_changes_behaviour(pipeline):
    """The eval sweeps tau_sim; this checks the knob is actually connected."""
    from conftest import make_settings

    strict = EntityResolver(pipeline.store, make_settings(tau_sim=0.99, tau_name=0.99))
    loose = EntityResolver(pipeline.store, make_settings(tau_sim=0.10, tau_name=0.10))
    pipeline.store.upsert_entity(_candidate(strict, "Helios Labs"))

    variant = Entity(
        entity_id=entity_key("Helios Lab", "organization"),
        canonical_name="Helios Lab",
        type="organization",
        embedding=pipeline.store.all_entities()[0].embedding,
    )
    assert strict.resolve(variant)[1] == "create"
    assert loose.resolve(variant)[1] == "merge"
