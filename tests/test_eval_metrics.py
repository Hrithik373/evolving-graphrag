"""Eval harness tests.

Two jobs: check the metric implementations, and check the benchmark itself is sound. A
benchmark whose "deleted" facts are also supported by a surviving document would report a
stale answer that never happened, so that property is asserted, not assumed.
"""

from __future__ import annotations

import pytest
from conftest import make_settings

from egraph.eval.baselines import SystemRegistry
from egraph.eval.benchmark import build_benchmark, build_scaled_benchmark
from egraph.eval.metrics import (
    aggregate,
    exact_match,
    is_abstention,
    score_question,
    token_f1,
)
from egraph.eval.runner import render_report
from egraph.eval.scenarios import run_scenarios


# ------------------------------------------------------------------ metric units
def test_token_f1_bounds():
    assert token_f1("the fjord protocol", "The Fjord Protocol") == 1.0
    assert token_f1("completely different words here", "the fjord protocol") == 0.0
    assert 0 < token_f1("the fjord protocol replaced sable", "the fjord protocol") < 1


def test_exact_match_ignores_case_articles_and_punctuation():
    assert exact_match("The Fjord Protocol.", "fjord protocol") == 1.0
    assert exact_match("Sable Protocol", "Fjord Protocol") == 0.0


def test_abstention_detection():
    assert is_abstention("The indexed sources do not contain this information.")
    assert not is_abstention("Marit Solberg founded Helios Labs.")


def test_score_question_flags_stale_facts():
    score = score_question(
        "Ines Duarte founded Kestrel Systems.",
        "The indexed sources do not contain this information.",
        must_exclude=("Ines Duarte",),
    )
    assert score.stale is True
    assert score.correct is False


def test_score_question_credits_required_facts():
    score = score_question(
        "Marit Solberg founded Helios Labs in 2019.",
        "Marit Solberg founded Helios Labs in 2019.",
        must_include=("Marit Solberg", "Helios Labs"),
    )
    assert score.recall == 1.0
    assert score.stale is False
    assert score.correct is True
    assert score.em == 1.0


def test_aggregate_of_nothing_is_zero():
    assert aggregate([]).n == 0


# ------------------------------------------------------------------ benchmark soundness
def test_benchmark_deletion_facts_are_uniquely_supported():
    """A must_exclude fact that a surviving document also states would score a correct
    system as stale. This is the benchmark's own correctness check."""
    benchmark = build_benchmark()
    surviving = {**benchmark.base, **benchmark.new}
    surviving.update(benchmark.updates)
    for doc_id in benchmark.deletions:
        surviving.pop(doc_id, None)
    corpus = " ".join(surviving.values()).lower()

    for question in benchmark.deletion_questions:
        for fact in question.must_exclude:
            assert fact.lower() not in corpus, (
                f"'{fact}' is still supported by a surviving document, so the deletion "
                f"question '{question.question}' cannot detect staleness"
            )


def test_benchmark_survivor_facts_really_do_survive():
    """The over-deletion control needs facts that a surviving document still supports."""
    benchmark = build_benchmark()
    surviving = {**benchmark.base, **benchmark.new}
    surviving.update(benchmark.updates)
    for doc_id in benchmark.deletions:
        surviving.pop(doc_id, None)
    corpus = " ".join(surviving.values()).lower()

    for question in benchmark.surviving_questions:
        for fact in question.must_include:
            assert (
                fact.lower() in corpus
            ), f"'{fact}' is not in the surviving corpus, so it cannot be required to survive"


def test_scaled_benchmark_grows_the_corpus_without_breaking_questions():
    small = build_benchmark()
    large = build_scaled_benchmark(3)
    assert len(large.base) == 3 * len(small.base)
    # Questions stay pinned to copy 0 so they remain answerable at every scale.
    assert large.base_questions == small.base_questions
    assert set(small.base) <= set(large.base)


# ------------------------------------------------------------------ end to end
@pytest.fixture(scope="module")
def outcomes():
    benchmark = build_benchmark()
    systems = SystemRegistry(make_settings()).build(
        ["evolving", "full-reindex", "append-only", "flat-vector"]
    )
    results = {}
    for name, system in systems.items():
        results[name] = {o.scenario: o for o in run_scenarios(system, benchmark)}
    return results


def test_append_only_serves_retracted_facts(outcomes):
    """The baseline this project exists to beat."""
    assert outcomes["append-only"]["deletion"].scores.stale_answer_rate == 1.0


def test_evolving_matches_full_reindex_freshness(outcomes):
    """The headline claim: same freshness as a rebuild."""
    ours = outcomes["evolving"]["deletion"].scores
    reference = outcomes["full-reindex"]["deletion"].scores
    assert ours.stale_answer_rate == 0.0
    assert ours.stale_answer_rate <= reference.stale_answer_rate


def test_evolving_does_not_over_delete(outcomes):
    """The control: deleting too much is as wrong as deleting too little."""
    ours = outcomes["evolving"]["deletion-survivors"].scores
    reference = outcomes["full-reindex"]["deletion-survivors"].scores
    assert ours.recall == 1.0
    assert ours.recall >= reference.recall


def test_evolving_is_substantially_cheaper_than_full_reindex(outcomes):
    ours = outcomes["evolving"]["base-on-updated"].update_cost["tokens"]
    reference = outcomes["full-reindex"]["base-on-updated"].update_cost["tokens"]
    assert ours < reference, "incremental maintenance must cost less than reindexing"
    assert reference / max(ours, 1) > 2, f"expected a large cost gap, got {reference}/{ours}"


def test_evolving_recomputes_fewer_summaries_than_a_rebuild(outcomes):
    ours = outcomes["evolving"]["base-on-updated"].update_cost["communities_recomputed"]
    reference = outcomes["full-reindex"]["base-on-updated"].update_cost["communities_recomputed"]
    assert ours < reference


def test_answer_quality_is_not_sacrificed_for_freshness(outcomes):
    """Cheap and fresh is worthless if the answers got worse."""
    for scenario in ("base-on-base", "base-on-updated", "new-on-updated"):
        ours = outcomes["evolving"][scenario].scores
        reference = outcomes["full-reindex"][scenario].scores
        assert ours.recall >= reference.recall - 1e-9
        assert ours.f1 >= reference.f1 - 1e-9


def test_graph_systems_beat_flat_vector_on_surviving_facts(outcomes):
    """Flat vector RAG deletes easily but cannot answer across documents."""
    assert (
        outcomes["evolving"]["deletion-survivors"].scores.recall
        > outcomes["flat-vector"]["deletion-survivors"].scores.recall
    )


def test_report_renders(outcomes):
    from egraph.eval.runner import EvalRun

    run = EvalRun(config_hash="deadbeefdeadbeef", seed=1337, started_at=0.0)
    for system in outcomes.values():
        run.scenarios.extend(system.values())
    text = render_report(run)
    assert "Scenario comparison" in text
    assert "append-only" in text


# ------------------------------------------------------------------ reproducibility
def test_the_eval_is_reproducible():
    """A seeded run must produce identical numbers every time.

    This caught a real bug: the recompute queue was ordered by `dirty_since`, and two
    communities marked in the same clock tick tie, so oldest-first silently alternated
    between time order and insertion order. Under a bounded recompute budget that changed
    *which* summaries were regenerated, and the Pareto curve moved between runs. The
    ordering key is now an explicit counter.
    """
    from egraph.eval.pareto import _run_point

    benchmark = build_benchmark()
    settings = make_settings()
    points = [_run_point(settings, benchmark, 1, "budget=1") for _ in range(3)]

    signatures = {
        (p.update_tokens, p.communities_recomputed, p.dirty_fraction_after, p.mean_stale_touched)
        for p in points
    }
    assert len(signatures) == 1, f"the same seeded run produced {len(signatures)} outcomes"


def test_dirty_queue_order_is_a_total_order(loaded):
    """Oldest-dirty-first must not depend on clock granularity."""
    from egraph.store.communities import mark_dirty

    ids = [c.community_id for c in loaded.store.all_communities()]
    mark_dirty(loaded.store, ids, reason="test")

    batches = [[c.community_id for c in loaded.store.dirty_communities()] for _ in range(3)]
    assert batches[0] == batches[1] == batches[2]
    seqs = [c.dirty_seq for c in loaded.store.dirty_communities()]
    assert seqs == sorted(seqs), "the queue must be ordered by the sequence key"
    assert len(set(seqs)) == len(seqs), "sequence numbers must be unique"
