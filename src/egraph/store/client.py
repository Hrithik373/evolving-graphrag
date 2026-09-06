"""ArcadeDB HTTP client: connection, database bootstrap, commands, retries."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ArcadeDBError(RuntimeError):
    pass


class ArcadeDBClient:
    """Thin wrapper over ArcadeDB's HTTP API.

    ArcadeDB distinguishes idempotent reads (``/api/v1/query``) from mutations
    (``/api/v1/command``); we keep that distinction so reads can be retried freely.
    """

    def __init__(
        self,
        url: str,
        database: str,
        user: str,
        password: str,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self.url = url.rstrip("/")
        self.database = database
        self.retries = retries
        self._client = httpx.Client(
            base_url=self.url,
            auth=(user, password),
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------ plumbing
    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, payload: dict[str, Any], retries: int | None = None) -> Any:
        attempts = self.retries if retries is None else retries
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.post(path, json=payload)
                if response.status_code >= 400:
                    raise ArcadeDBError(
                        f"{response.status_code} on {path}: {response.text[:500]} :: {payload}"
                    )
                if not response.content:
                    return None
                return response.json()
            except (httpx.TransportError, httpx.TimeoutException) as exc:  # network flake
                last = exc
                time.sleep(0.25 * (2**attempt))
        raise ArcadeDBError(f"ArcadeDB unreachable at {self.url}: {last}")

    # ------------------------------------------------------------------ api
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"language": "sql", "command": sql}
        if params:
            payload["params"] = params
        body = self._post(f"/api/v1/query/{self.database}", payload)
        return (body or {}).get("result", [])

    def command(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"language": "sql", "command": sql}
        if params:
            payload["params"] = params
        body = self._post(f"/api/v1/command/{self.database}", payload, retries=1)
        return (body or {}).get("result", [])

    def script(self, statements: list[str]) -> None:
        """Run DDL one statement at a time. ArcadeDB's SQL script mode is picky about DDL,
        and schema creation is not hot-path, so simplicity wins."""
        for statement in statements:
            stmt = statement.strip().rstrip(";")
            if not stmt or stmt.startswith("--"):
                continue
            try:
                self.command(stmt)
            except ArcadeDBError as exc:
                # "already exists" is the expected outcome of a re-run migration.
                if "already exist" in str(exc).lower() or "duplicated" in str(exc).lower():
                    continue
                raise

    def ensure_database(self) -> None:
        body = self._post("/api/v1/server", {"command": "list databases"}) or {}
        databases = body.get("result", [])
        names = {d if isinstance(d, str) else d.get("name") for d in databases}
        if self.database not in names:
            log.info("creating ArcadeDB database %s", self.database)
            self._post("/api/v1/server", {"command": f"create database {self.database}"})

    def ping(self) -> bool:
        try:
            self._post("/api/v1/server", {"command": "list databases"}, retries=1)
            return True
        except Exception:  # noqa: BLE001 - readiness probe must never raise
            return False
