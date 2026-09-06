"""LLM gateway.

Three backends behind one metered interface:

* ``mock``      - the deterministic rule-based backend in ``gateway/mock.py``. Default, so
                  tests, the eval harness and ``make demo`` need no API key.
* ``anthropic`` - the official Anthropic SDK, schema-constrained via ``output_config.format``.
* ``gateway``   - your existing FastAPI proxy, called over HTTP with the same contract.

Every path returns ``(payload, usage)`` and every call is priced by :class:`CostMeter`.
No module anywhere else in this codebase is allowed to call a model directly.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from egraph.cost.meter import CostMeter
from egraph.gateway import mock, prompts
from egraph.settings import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    wall_ms: float = 0.0
    model: str = "mock"


def estimate_tokens(text: str) -> int:
    """Rough token count for the mock backend's cost model.

    Real backends report exact usage; this keeps the mock's cost axis on the same scale
    (~4 characters per token) so offline Pareto curves are comparable in shape.
    """
    return max(1, len(text) // 4)


class LLMClient:
    def __init__(self, meter: CostMeter, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.meter = meter
        self._http: httpx.Client | None = None
        self._anthropic: Any = None

    # ------------------------------------------------------------------ public API
    def extract(self, text: str, doc_id: str | None = None) -> dict:
        payload, usage = self._json_call(
            operation="extract",
            system=prompts.EXTRACTION_SYSTEM,
            user=prompts.extraction_prompt(text),
            schema=prompts.EXTRACTION_SCHEMA,
            effort=self.settings.llm_effort_extract,
            offline=lambda: mock.extract(text),
        )
        self.meter.record(
            "extract",
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            wall_ms=usage.wall_ms,
            model=usage.model,
            doc_id=doc_id,
        )
        payload.setdefault("entities", [])
        payload.setdefault("relations", [])
        return payload

    def summarise(
        self,
        entity_lines: list[str],
        relation_lines: list[str],
        community_id: str | None = None,
    ) -> dict:
        payload, usage = self._json_call(
            operation="summarize",
            system=prompts.SUMMARY_SYSTEM,
            user=prompts.summary_prompt(entity_lines, relation_lines),
            schema=prompts.SUMMARY_SCHEMA,
            effort=self.settings.llm_effort_summarize,
            offline=lambda: mock.summarise(entity_lines, relation_lines),
        )
        self.meter.record(
            "summarize",
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            wall_ms=usage.wall_ms,
            model=usage.model,
            community_id=community_id,
        )
        return {
            "title": str(payload.get("title", ""))[:120],
            "summary": str(payload.get("summary", "")),
        }

    def answer(self, question: str, context: str) -> str:
        text, usage = self._text_call(
            operation="answer",
            system=prompts.ANSWER_SYSTEM,
            user=prompts.answer_prompt(question, context),
            offline=lambda: mock.answer(question, context),
        )
        self.meter.record(
            "answer",
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            wall_ms=usage.wall_ms,
            model=usage.model,
        )
        return text

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    # ------------------------------------------------------------------ backends
    def _json_call(
        self,
        operation: str,
        system: str,
        user: str,
        schema: dict,
        effort: str,
        offline,
    ) -> tuple[dict, Usage]:
        started = time.perf_counter()
        backend = self.settings.llm_backend
        if backend == "mock":
            payload = offline()
            wall = (time.perf_counter() - started) * 1000
            return payload, Usage(
                tokens_in=estimate_tokens(system + user),
                tokens_out=estimate_tokens(json.dumps(payload)),
                wall_ms=wall,
                model="mock",
            )
        if backend == "anthropic":
            raw, usage = self._anthropic_call(operation, system, user, schema, effort)
        else:
            raw, usage = self._proxy_call(operation, system, user, schema, effort)
        return _loads(raw), usage

    def _text_call(self, operation: str, system: str, user: str, offline) -> tuple[str, Usage]:
        started = time.perf_counter()
        backend = self.settings.llm_backend
        if backend == "mock":
            text = offline()
            wall = (time.perf_counter() - started) * 1000
            return text, Usage(
                tokens_in=estimate_tokens(system + user),
                tokens_out=estimate_tokens(text),
                wall_ms=wall,
                model="mock",
            )
        if backend == "anthropic":
            return self._anthropic_call(operation, system, user, None, "medium")
        return self._proxy_call(operation, system, user, None, "medium")

    def _anthropic_call(
        self, operation: str, system: str, user: str, schema: dict | None, effort: str
    ) -> tuple[str, Usage]:
        client = self._anthropic_client()
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "max_tokens": self.settings.llm_max_tokens,
            # The system prompt is byte-stable across every chunk, so cache the prefix and
            # let only the passage vary. This is the single biggest cost lever on ingest.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": effort},
        }
        if schema is not None:
            request["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        started = time.perf_counter()
        response = client.messages.create(**request)
        wall = (time.perf_counter() - started) * 1000
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "category", None)
            raise RuntimeError(f"model declined {operation} request (category={detail})")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return text, Usage(
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            wall_ms=wall,
            model=self.settings.llm_model,
        )

    def _anthropic_client(self):
        if self._anthropic is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "EGRAPH_LLM_BACKEND=anthropic needs the SDK: pip install anthropic"
                ) from exc
            self._anthropic = anthropic.Anthropic(timeout=self.settings.llm_timeout_s)
        return self._anthropic

    def _proxy_call(
        self, operation: str, system: str, user: str, schema: dict | None, effort: str
    ) -> tuple[str, Usage]:
        """Call the project's own FastAPI LLM proxy.

        Contract: ``POST {gateway_url}/v1/complete`` -> ``{"text": str, "usage": {...}}``.
        The proxy owns provider credentials; this client owns metering.
        """
        http = self._http_client()
        body: dict[str, Any] = {
            "operation": operation,
            "system": system,
            "prompt": user,
            "model": self.settings.llm_model,
            "max_tokens": self.settings.llm_max_tokens,
            "effort": effort,
        }
        if schema is not None:
            body["json_schema"] = schema
        started = time.perf_counter()
        response = http.post("/v1/complete", json=body)
        response.raise_for_status()
        wall = (time.perf_counter() - started) * 1000
        data = response.json()
        usage = data.get("usage", {}) or {}
        return str(data.get("text", "")), Usage(
            tokens_in=int(usage.get("input_tokens", estimate_tokens(system + user))),
            tokens_out=int(usage.get("output_tokens", estimate_tokens(str(data.get("text", ""))))),
            wall_ms=wall,
            model=str(data.get("model", self.settings.llm_model)),
        )

    def _http_client(self) -> httpx.Client:
        if self._http is None:
            headers = {"Content-Type": "application/json"}
            if self.settings.gateway_api_key:
                headers["Authorization"] = f"Bearer {self.settings.gateway_api_key}"
            self._http = httpx.Client(
                base_url=self.settings.gateway_url.rstrip("/"),
                timeout=self.settings.llm_timeout_s,
                headers=headers,
            )
        return self._http


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _loads(raw: str) -> dict:
    """Parse a JSON payload. Schema-constrained responses parse directly; a proxy that
    wraps the JSON in prose still yields its object rather than failing the ingest."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(raw or "")
        if not match:
            log.warning("model returned unparseable payload: %r", (raw or "")[:200])
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            log.warning("model returned malformed JSON: %r", match.group(0)[:200])
            return {}
