# evolving-graphrag
#
# Two modes, deliberately:
#   local  - memory store, inline queue, mock LLM. No daemons, no API key, seconds to run.
#   docker - the real topology: ArcadeDB + Redis + api + worker + Prometheus + Grafana.
#
# Everything reproducible is a target here; nothing important is a shell incantation that
# lives only in a README.

SHELL := /bin/bash
PY ?= python
VENV ?= .venv
BIN := $(VENV)/$(shell test -d $(VENV)/Scripts && echo Scripts || echo bin)
PYTHON := $(BIN)/python
COMPOSE ?= docker compose
RESULTS ?= results

.DEFAULT_GOAL := help
.PHONY: help install setup test test-fast lint fmt typecheck smoke demo ingest churn query \
        staleness stats integrity eval eval-quick figures dev api worker up down logs ps \
        restart rebuild backup restore clean frontend frontend-build frontend-install \
        docker-test seed check

## ----------------------------------------------------------------- help
help: ## Show this help
	@echo "evolving-graphrag"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## ----------------------------------------------------------------- setup
install: ## Create the venv and install the package with dev extras
	$(PY) -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
	@echo "installed. optional: $(PYTHON) -m pip install -e '.[leiden,embeddings]'"

setup: install frontend-install ## Full local setup, backend and frontend

frontend-install: ## Install the frontend dependencies
	cd frontend && npm install --no-audit --no-fund

## ----------------------------------------------------------------- quality
test: ## Run the whole test suite (offline, no daemons)
	$(PYTHON) -m pytest -q

test-fast: ## Skip the property-based tests
	$(PYTHON) -m pytest -q --ignore=tests/test_churn_properties.py

lint: ## ruff + black --check
	$(PYTHON) -m ruff check src tests fixtures scripts
	$(PYTHON) -m black --check src tests fixtures scripts

fmt: ## Format the code
	$(PYTHON) -m ruff check --fix src tests fixtures scripts
	$(PYTHON) -m black src tests fixtures scripts

typecheck: ## mypy over the package
	$(PYTHON) -m mypy src/egraph

check: lint test ## Lint and test - what CI runs

## ----------------------------------------------------------------- local runs
smoke: ## End-to-end ingest -> query -> update -> delete, printed
	$(PYTHON) scripts/smoke.py

demo: ## Scripted churn demo over the bundled mini-corpus
	$(PYTHON) -m egraph.cli demo

ingest: ## Ingest a directory: make ingest DIR=path/to/docs
	$(PYTHON) -m egraph.cli ingest $(or $(DIR),fixtures/mini_corpus)

seed: ## Load the mini-corpus into the running stack (docker) or local store
	$(PYTHON) scripts/seed.py

churn: ## Drive a live add/update/delete against a running API
	$(PYTHON) scripts/churn_demo.py

query: ## Ask a question: make query Q="Who founded Helios Labs?"
	$(PYTHON) -m egraph.cli query "$(or $(Q),Who founded Helios Labs?)"

staleness: ## Current index freshness
	$(PYTHON) -m egraph.cli staleness

stats: ## Index size and cost to date
	$(PYTHON) -m egraph.cli stats

integrity: ## Check the provenance invariants against the live index
	$(PYTHON) -m egraph.cli integrity

## ----------------------------------------------------------------- evaluation
eval: ## Full benchmark: every table and figure, from scratch
	$(PYTHON) -m egraph.cli eval --output $(RESULTS)
	@echo ""
	@echo "wrote $(RESULTS)/report.md - the tables for the write-up"

eval-quick: ## Same, with fewer sweep points
	$(PYTHON) -m egraph.cli eval --output $(RESULTS) --quick

figures: eval ## Regenerate the figures from the eval output
	$(PYTHON) scripts/make_figures.py --results $(RESULTS)

## ----------------------------------------------------------------- services (local)
dev: ## Run the API and the console together (one Ctrl+C stops both)
	$(PYTHON) scripts/dev.py $(ARGS)

api: ## Run the API on :8000 against the local store
	$(BIN)/uvicorn egraph.api.app:app --reload --host 0.0.0.0 --port 8000

worker: ## Run an arq worker (needs Redis)
	$(BIN)/arq egraph.workers.worker.WorkerSettings

frontend: ## Run the frontend dev server on :5173, proxying to :8000
	cd frontend && npm run dev

frontend-build: ## Production build of the frontend
	cd frontend && npm run build

## ----------------------------------------------------------------- docker
up: ## Bring the whole stack up
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  api        http://localhost:8000/docs"
	@echo "  console    http://localhost:5173"
	@echo "  grafana    http://localhost:3000  (anonymous viewer enabled)"
	@echo "  prometheus http://localhost:9090"
	@echo "  arcadedb   http://localhost:2480"

down: ## Stop the stack, keeping the volumes
	$(COMPOSE) down

rebuild: ## Rebuild the images and restart
	$(COMPOSE) up -d --build --force-recreate

restart: down up ## Full restart

logs: ## Tail the logs: make logs S=worker
	$(COMPOSE) logs -f $(or $(S),)

ps: ## Show the running services and health
	$(COMPOSE) ps

docker-test: ## Run the test suite inside the api image
	$(COMPOSE) run --rm --entrypoint "" api python -m pytest -q

scale: ## Scale the workers - the throughput knob: make scale N=4
	$(COMPOSE) up -d --scale worker=$(or $(N),2)

## ----------------------------------------------------------------- operations
backup: ## Snapshot the ArcadeDB and Redis volumes before a demo
	@mkdir -p backups
	$(COMPOSE) exec -T arcadedb tar czf - /home/arcadedb/databases > backups/arcadedb-$$(date +%Y%m%d-%H%M%S).tar.gz
	$(COMPOSE) exec -T redis redis-cli SAVE >/dev/null
	@echo "wrote backups/"

restore: ## Restore the newest ArcadeDB snapshot: make restore F=backups/x.tar.gz
	@test -n "$(F)" || (echo "usage: make restore F=backups/<file>.tar.gz" && exit 1)
	cat $(F) | $(COMPOSE) exec -T arcadedb tar xzf - -C /
	$(COMPOSE) restart arcadedb

clean: ## Remove build artefacts and local state (keeps docker volumes)
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build data
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo "cleaned. 'docker compose down -v' also drops the volumes."
