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
    # Strip comments FIRST, then split on the statement terminator. Splitting first is a
    # trap: a comment containing a semicolon gets cut in half and its tail is left looking
    # like SQL, which the server then rejects with a parse error pointing at prose.
    body = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("--"))
    return [stmt for chunk in body.split(";") if (stmt := chunk.strip())]


def migrate(client: ArcadeDBClient, path: Path | None = None) -> int:
    """Create database + types + indexes. Safe to run on every boot."""
    client.ensure_database()
    statements = load_schema_statements(path)
    client.script(statements)
    log.info("applied %d schema statements to %s", len(statements), client.database)
    return len(statements)
