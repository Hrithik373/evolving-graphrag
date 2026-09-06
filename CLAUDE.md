# Project conventions — evolving-graphrag

## What this project is
A knowledge-graph RAG service whose contribution is the **maintenance layer**, not answer
quality. When a document changes, the system touches the minimal affected subgraph and the
minimal set of community summaries, and never rebuilds. Read
`EVOLVING_GRAPHRAG_BLUEPRINT.md` for the full design.

## Golden rules

- **PROVENANCE IS SACRED.** Every entity, relation and summary must trace to chunk ids. No
  mutation may create graph state without provenance. Deletion works by walking provenance
  — never by rebuild.
  - This extends to *derived* state. An entity's `description` is written out of a
    particular chunk, so it lives in `descriptions: {chunk_id: text}` and is re-derived
    whenever mentions change. A survivor that keeps prose from a deleted document is a
    provenance leak, and it is the exact bug `tests/test_provenance.py` was extended to
    catch.
- **The write path is ASYNC.** API handlers must not block on an LLM call. Extraction,
  embedding, summarisation and compaction happen in workers. If a handler calls the model
  inline, that is a bug. The one deliberate exception: `DELETE` completes its provenance
  walk synchronously, because retraction needs no model.
- **SELECTIVE, NOT GLOBAL.** Only communities with a dirty member get re-summarised. Never
  re-summarise the whole graph on a single-document change. Only compaction touches
  everything, and only on schedule — and even compaction matches new clusters to existing
  communities by membership overlap so a stable community keeps its id, its summary and its
  clean flag.
- **`content_hash` makes ingest idempotent.** Re-adding an unchanged document is a no-op
  that spends zero tokens. Test it.
- **Chunk ids are content-addressed and position-independent.** That is what lets an update
  reuse the paragraphs it did not touch. Never put a position into a chunk id, and keep
  `chunk_overlap` at 0 by default — overlapping chunks copy their predecessor's tail, so an
  edit to one paragraph changes the id of the next and destroys reuse.

## Build order
Follow the blueprint phases P0→P5. Tests ship in the same commit as the code. The churn
engine (`src/egraph/churn/`) needs property-based tests, not just examples — see
`tests/test_churn_properties.py` for the four invariants (provenance closure, deletion
minimality, idempotence, order independence).

## Style
Python 3.11+, ruff + black (100 cols), type hints required. All cross-module payloads are
Pydantic models from `src/egraph/schemas`. Every LLM/embedding call goes through
`gateway/client.py` and is metered by `CostMeter` — **no unmetered model calls anywhere**,
including cache hits, which are recorded as zero-cost calls rather than absent ones.

Frontend is React + TypeScript with `strict` on. Charts follow the validated data-viz
palette in `frontend/src/styles/app.css`: categorical hues in fixed slot order, never
cycled; one y-axis; status colours reserved and always paired with a label.

## Reproducibility
Config via pydantic-settings + `.env`, nothing hard-coded. Every eval run is seeded and
stamped with a `config_hash`. `make eval` regenerates every table and figure from scratch.

## Backends and why they exist
- `EGRAPH_LLM_BACKEND=mock` is a real rule-based extractor/summariser/answerer, not a stub.
  It exists so the churn semantics are provable in CI with no API key and no GPU. When you
  change extraction behaviour, change it there too or the tests stop meaning anything.
- `EGRAPH_STORE_BACKEND=memory` is the semantics oracle. `postgres` and `arcadedb` must
  behave identically to it and to each other; `tests/test_store_parity.py` runs every test
  once per backend and is the only thing standing between the report and a system nobody is
  running. Adding a store method means adding it to all three.
- **Postgres is the deployable backend.** ArcadeDB is the only component with no managed
  offering, so a deployment that needs to be cheap or free runs on Postgres. It is not a
  downgrade: the provenance walk is an array-containment query against a GIN index, so
  retraction is an index scan.
- **SQL schema files: strip comments before splitting on `;`.** A comment containing a
  semicolon gets cut in half and its tail parsed as SQL. This actually happened.
- **ArcadeDB: never `ORDER BY` with an implicit projection.** `SELECT FROM T ORDER BY x`
  returns one phantom row per storage bucket. Sort in Python instead — the result sets that
  need ordering are small, and Python's sort is deterministic, which reproducibility needs
  anyway.
- Compaction prefers `leidenalg`; the pure-Python Louvain in `cluster/louvain.py` is the
  fallback. Label propagation is kept for the ablation only — on a small dense graph it
  collapses everything into one community, which would make compaction destructive.

## Receipts must be true
A `ChurnResult` is what the UI shows the user, so it has to describe what the write
actually did. Two rules that were learned the hard way:

- If the queue does not defer (`queue.synchronous`), the extraction already ran by the time
  the handler returns, so its mutations belong in the receipt. Checking `queue is None` is
  not enough - the API always has an `InlineQueue`, never `None`.
- `dirty_communities` is what *this* change dirtied, not everything currently outstanding.
  Snapshot the dirty set before the write and diff.

`tests/test_api.py` builds its pipeline exactly as `api/deps.py` does, queue included. A
fixture that wires it differently tests a path the server never takes.

## Don't
- Don't add a second vector DB. ArcadeDB holds graph and vectors.
- Don't recompute all summaries on a small change.
- Don't let deletion fall back to a rebuild.
- Don't let a benchmark `must_exclude` fact be supported by a surviving document — that
  scores a correct system as stale. `tests/test_eval_metrics.py` asserts this.
- Don't give a baseline this project's optimisations. The full-reindex baseline drops its
  extraction cache on every rebuild on purpose; crediting it with a content-hash cache would
  be crediting it with the contribution being evaluated.
