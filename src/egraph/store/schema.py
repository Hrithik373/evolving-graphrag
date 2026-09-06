"""Apply config/graph_schema.sql to an ArcadeDB database."""

from __future__ import annotations

import logging
from pathlib import Path

from egraph.store.client import ArcadeDBClient

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parents[3] / "config" / "graph_schema.sql"


def load_schema_statements(path: Path | None = None) -> list[str]:
    source = path or SCHEMA_FILE
    if not source.exists():  # installed as a wheel without the config dir
        return []
    raw = source.read_text(encoding="utf-8")
    statements = []
    for chunk in raw.split(";"):
        stmt = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if stmt:
            statements.append(stmt)
    return statements


def migrate(client: ArcadeDBClient, path: Path | None = None) -> int:
    """Create database + types + indexes. Safe to run on every boot."""
    client.ensure_database()
    statements = load_schema_statements(path)
    client.script(statements)
    log.info("applied %d schema statements to %s", len(statements), client.database)
    return len(statements)
