"""Cost accounting.

Golden rule: no model call exists unless it produced a :class:`CostRecord`. The
cost-vs-freshness Pareto is computed straight off these rows, so an unmetered call is a
silently wrong figure in the report.
"""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field

from egraph.observability import metrics
from egraph.schemas import CostRecord
from egraph.settings import Settings, get_settings
from egraph.store.base import GraphStore


@dataclass
class CostTally:
    """In-process running total. The store holds the durable copy."""

    tokens_in: int = 0
    tokens_out: int = 0
    wall_ms: float = 0.0
    usd: float = 0.0
    calls: int = 0
    cache_hits: int = 0
    by_operation: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def add(self, record: CostRecord) -> None:
        self.tokens_in += record.tokens_in
        self.tokens_out += record.tokens_out
        self.wall_ms += record.wall_ms
        self.usd += record.usd
        self.calls += 1
        if record.cache_hit:
            self.cache_hits += 1
        self.by_operation[record.operation] += record.tokens


class CostMeter:
    """Wraps every LLM/embedding call: prices it, stores it, exports it to Prometheus."""

    def __init__(self, store: GraphStore | None = None, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.tally = CostTally()

    def price(self, tokens_in: int, tokens_out: int, kind: str = "llm") -> float:
        if kind == "embed":
            return (tokens_in / 1_000_000) * self.settings.embed_price_per_mtok
        return (tokens_in / 1_000_000) * self.settings.price_in_per_mtok + (
            tokens_out / 1_000_000
        ) * self.settings.price_out_per_mtok

    def record(
        self,
        operation: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        wall_ms: float = 0.0,
        model: str = "",
        cache_hit: bool = False,
        kind: str = "llm",
        doc_id: str | None = None,
        community_id: str | None = None,
    ) -> CostRecord:
        record = CostRecord(
            operation=operation,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            wall_ms=wall_ms,
            usd=0.0 if cache_hit else self.price(tokens_in, tokens_out, kind=kind),
            model=model or self.settings.llm_model,
            cache_hit=cache_hit,
            doc_id=doc_id,
            community_id=community_id,
        )
        self.tally.add(record)
        if self.store is not None:
            self.store.record_cost(record)
        metrics.observe_cost(record)
        return record

    @contextmanager
    def timed(self, operation: str, **kwargs):
        """Time a block and record it. Yields a mutable dict for token counts.

        >>> with meter.timed("extract") as slot:
        ...     slot["tokens_in"] = 120
        """
        slot: dict[str, int] = {"tokens_in": 0, "tokens_out": 0}
        started = time.perf_counter()
        try:
            yield slot
        finally:
            self.record(
                operation,
                tokens_in=int(slot.get("tokens_in", 0)),
                tokens_out=int(slot.get("tokens_out", 0)),
                wall_ms=(time.perf_counter() - started) * 1000,
                **kwargs,
            )

    def snapshot(self) -> dict[str, float | int]:
        return {
            "calls": self.tally.calls,
            "tokens": self.tally.tokens,
            "tokens_in": self.tally.tokens_in,
            "tokens_out": self.tally.tokens_out,
            "usd": round(self.tally.usd, 6),
            "wall_ms": round(self.tally.wall_ms, 2),
            "cache_hits": self.tally.cache_hits,
        }

    def reset(self) -> None:
        self.tally = CostTally()
