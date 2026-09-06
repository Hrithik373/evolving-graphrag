# syntax=docker/dockerfile:1
# One image, two roles. The api and the worker run the same code and the same pipeline;
# only the entrypoint differs. That is deliberate - it is what makes "the eval measured the
# thing the service runs" a true statement rather than a hope.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first so a source edit does not invalidate the install layer.
COPY pyproject.toml README.md ./
COPY src/egraph/__init__.py src/egraph/__init__.py
RUN pip install --upgrade pip && pip install -e "."

# Leiden is optional: if the wheels are unavailable for this platform the build still
# succeeds and compaction falls back to the bundled pure-Python Louvain.
RUN pip install "python-igraph>=0.11" "leidenalg>=0.10" || \
    echo "leidenalg unavailable - compaction will use the Louvain fallback"

COPY src/ src/
COPY config/ config/
COPY fixtures/ fixtures/
COPY scripts/ scripts/

RUN useradd --create-home --uid 10001 egraph \
 && mkdir -p /app/data /app/results \
 && chown -R egraph:egraph /app
USER egraph

EXPOSE 8000 9100

# ---------------------------------------------------------------------------- api
FROM base AS api
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["uvicorn", "egraph.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ------------------------------------------------------------------------- worker
FROM base AS worker
# arq has no HTTP surface; the metrics exporter is the liveness signal.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:9100/metrics || exit 1
CMD ["arq", "egraph.workers.worker.WorkerSettings"]
