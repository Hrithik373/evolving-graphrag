"""The four evaluation scenarios, run against any :class:`System`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from egraph.eval.baselines import System
from egraph.eval.benchmark import ChurnBenchmark
from egraph.eval.metrics import Aggregate, QuestionScore, aggregate, score_question
from fixtures.mini_corpus import Question

log = logging.getLogger(__name__)


@dataclass
class ScenarioOutcome:
    system: str
    scenario: str
    scores: Aggregate
    per_question: list[dict] = field(default_factory=list)
    update_cost: dict = field(default_factory=dict)
    index: dict = field(default_factory=dict)
    staleness: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "system": self.system,
            "scenario": self.scenario,
            **self.scores.as_dict(),
            "update_cost": self.update_cost,
            "index": self.index,
            "staleness": self.staleness,
            "per_question": self.per_question,
        }


def ask(system: System, questions: list[Question]) -> tuple[Aggregate, list[dict]]:
    scores: list[QuestionScore] = []
    rows: list[dict] = []
    for question in questions:
        answer = system.query(question.question)
        score = score_question(
            answer.text, question.answer, question.must_include, question.must_exclude
        )
        score.question = question.question
        scores.append(score)
        rows.append(
            {
                "question": question.question,
                "prediction": answer.text[:400],
                "reference": question.answer,
                "f1": round(score.f1, 4),
                "recall": round(score.recall, 4),
                "stale": score.stale,
                "abstained": score.abstained,
                "citations": answer.citations,
                "stale_communities_touched": answer.stale_communities_touched,
            }
        )
    return aggregate(scores), rows


def _snapshot(system: System) -> tuple[dict, dict, dict]:
    stats = system.stats()
    staleness: dict = {}
    pipeline = getattr(system, "pipeline", None)
    if pipeline is not None:
        staleness = pipeline.staleness().model_dump(mode="json")
    return (
        system.cost().as_dict(),
        {
            "entities": stats.entities,
            "relations": stats.relations,
            "communities": stats.communities,
            "chunks": stats.chunks,
            "active_documents": stats.active_documents,
        },
        staleness,
    )


def run_scenarios(
    system: System,
    benchmark: ChurnBenchmark,
    on_phase: Callable[[str, System], None] | None = None,
) -> list[ScenarioOutcome]:
    """Drive one system through the full time-split and score it at each checkpoint."""
    outcomes: list[ScenarioOutcome] = []

    # ---------------------------------------------------------------- t0: build
    system.reset_cost()
    for change in benchmark.base_changes():
        system.apply(change)
    system.settle()
    if on_phase:
        on_phase("base", system)
    build_cost, index, staleness = _snapshot(system)

    scores, rows = ask(system, benchmark.base_questions)
    outcomes.append(
        ScenarioOutcome(
            system=system.name,
            scenario="base-on-base",
            scores=scores,
            per_question=rows,
            update_cost=build_cost,
            index=index,
            staleness=staleness,
        )
    )

    # ---------------------------------------------------------------- t1: churn
    system.reset_cost()
    for change in benchmark.churn_changes():
        system.apply(change)
    system.settle()
    if on_phase:
        on_phase("churn", system)
    churn_cost, index, staleness = _snapshot(system)

    # Historical stability: do the original questions still work after the corpus moved?
    scores, rows = ask(system, benchmark.base_questions)
    outcomes.append(
        ScenarioOutcome(
            system=system.name,
            scenario="base-on-updated",
            scores=scores,
            per_question=rows,
            update_cost=churn_cost,
            index=index,
            staleness=staleness,
        )
    )

    # Did the new information actually reach the index?
    scores, rows = ask(system, benchmark.new_questions)
    outcomes.append(
        ScenarioOutcome(
            system=system.name,
            scenario="new-on-updated",
            scores=scores,
            per_question=rows,
            update_cost=churn_cost,
            index=index,
            staleness=staleness,
        )
    )

    # ---------------------------------------------------------------- t2: deletion
    system.reset_cost()
    for change in benchmark.deletion_changes():
        system.apply(change)
    system.settle()
    if on_phase:
        on_phase("deletion", system)
    delete_cost, index, staleness = _snapshot(system)

    scores, rows = ask(system, benchmark.deletion_questions)
    outcomes.append(
        ScenarioOutcome(
            system=system.name,
            scenario="deletion",
            scores=scores,
            per_question=rows,
            update_cost=delete_cost,
            index=index,
            staleness=staleness,
        )
    )

    # The over-deletion control: facts a second document still supports must survive.
    scores, rows = ask(system, benchmark.surviving_questions)
    outcomes.append(
        ScenarioOutcome(
            system=system.name,
            scenario="deletion-survivors",
            scores=scores,
            per_question=rows,
            update_cost=delete_cost,
            index=index,
            staleness=staleness,
        )
    )
    return outcomes
