"""Evaluation metrics.

Two families, and the second is the one the project is actually about:

* **Quality** - token F1 and exact match against a reference answer, plus fact recall
  (did the required fact appear at all).
* **Freshness** - stale-answer rate: after a document is deleted, how often does the system
  still answer with a fact only that document supported. This is the metric where an
  append-only incremental index fails and a full reindex succeeds at great cost, and it is
  where this system is claimed to match full-reindex freshness at incremental cost.
"""

from __future__ import annotations

import re
import string
from collections import Counter
from dataclasses import dataclass

ARTICLES = re.compile(r"\b(a|an|the)\b")
WHITESPACE = re.compile(r"\s+")
PUNCT_TABLE = str.maketrans("", "", string.punctuation)

NO_ANSWER_MARKERS = (
    "do not contain",
    "does not contain",
    "no information",
    "not available in",
)


def normalise(text: str) -> str:
    text = text.lower().translate(PUNCT_TABLE)
    text = ARTICLES.sub(" ", text)
    return WHITESPACE.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return normalise(text).split()


def exact_match(prediction: str, reference: str) -> float:
    return float(normalise(prediction) == normalise(reference))


def token_f1(prediction: str, reference: str) -> float:
    pred, ref = tokens(prediction), tokens(reference)
    if not pred or not ref:
        return float(pred == ref)
    common = Counter(pred) & Counter(ref)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred)
    recall = overlap / len(ref)
    return 2 * precision * recall / (precision + recall)


def contains_fact(prediction: str, fact: str) -> bool:
    """Substring match on normalised text, so casing and punctuation do not decide truth."""
    return normalise(fact) in normalise(prediction)


def is_abstention(prediction: str) -> bool:
    lowered = prediction.lower()
    return any(marker in lowered for marker in NO_ANSWER_MARKERS)


@dataclass
class QuestionScore:
    question: str
    prediction: str
    f1: float
    em: float
    recall: float  # fraction of must_include facts that appeared
    stale: bool  # a must_exclude fact appeared -> retracted information served
    abstained: bool

    @property
    def correct(self) -> bool:
        return self.recall == 1.0 and not self.stale


def score_question(
    prediction: str,
    reference: str,
    must_include: tuple[str, ...] = (),
    must_exclude: tuple[str, ...] = (),
) -> QuestionScore:
    included = [fact for fact in must_include if contains_fact(prediction, fact)]
    leaked = [fact for fact in must_exclude if contains_fact(prediction, fact)]
    return QuestionScore(
        question="",
        prediction=prediction,
        f1=token_f1(prediction, reference),
        em=exact_match(prediction, reference),
        recall=(len(included) / len(must_include)) if must_include else 1.0,
        stale=bool(leaked),
        abstained=is_abstention(prediction),
    )


@dataclass
class Aggregate:
    n: int = 0
    f1: float = 0.0
    em: float = 0.0
    recall: float = 0.0
    stale_answer_rate: float = 0.0
    abstention_rate: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "n": self.n,
            "f1": round(self.f1, 4),
            "em": round(self.em, 4),
            "recall": round(self.recall, 4),
            "stale_answer_rate": round(self.stale_answer_rate, 4),
            "abstention_rate": round(self.abstention_rate, 4),
        }


def aggregate(scores: list[QuestionScore]) -> Aggregate:
    if not scores:
        return Aggregate()
    n = len(scores)
    return Aggregate(
        n=n,
        f1=sum(s.f1 for s in scores) / n,
        em=sum(s.em for s in scores) / n,
        recall=sum(s.recall for s in scores) / n,
        stale_answer_rate=sum(1 for s in scores if s.stale) / n,
        abstention_rate=sum(1 for s in scores if s.abstained) / n,
    )


def memory_footprint(stats) -> dict[str, int]:
    """Index size, which is the memory-growth-vs-corpus figure in the report."""
    return {
        "entities": stats.entities,
        "relations": stats.relations,
        "communities": stats.communities,
        "chunks": stats.chunks,
    }
