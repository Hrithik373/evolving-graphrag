#!/usr/bin/env bash
# Start the API and the console together.
#   ./start.sh                     memory store, mock LLM, seeded
#   ./start.sh --store postgres    against a real database
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  echo "No virtualenv found. Run:  make install" >&2
  exit 1
fi
exec "$PY" scripts/dev.py "$@"
