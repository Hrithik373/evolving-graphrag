# evolving-graphrag

A knowledge-graph RAG service that **maintains its graph and community summaries
incrementally under document churn** — add, update, delete — instead of rebuilding.

The contribution is the maintenance layer, not answer quality. Every design decision serves
one goal: when a document changes, touch the minimal affected subgraph and the minimal set
of summaries, and never rebuild.

```
                        POST /documents   PUT /documents/{id}   DELETE /documents/{id}
                                          POST /query   GET /index/staleness
                                                  │
                    ┌─────────────────────────────┴──────────────────────────────┐
                    │                                                            │
            ┌───────▼────────┐  fast: write intent,               ┌──────────────▼────────┐
            │  CHURN ENGINE  │  mark dirty, enqueue               │   RETRIEVAL ENGINE    │
            │ diff → mutate  │────────────┐                       │ local entities  ⊕     │
            │ → mark dirty   │            │                       │ global community      │
            └───────┬────────┘            ▼                       │ summaries             │
                    │              ┌─────────────┐  ┌──────────┐  └───────────┬───────────┘
                    │              │ REDIS QUEUE │─▶│ WORKERS  │              │
                    │              └─────────────┘  │ extract  │              │
                    │                               │ summarise│              │
                    │                               │ compact  │              │
                    ▼                               └────┬─────┘              │
        ┌───────────────────────────────────────────────────────────┐         │
        │        ARCADEDB — graph + vectors in one store            │◀────────┘
        │  SourceDocument · Chunk · Entity · Community               │
        │  MENTIONS · RELATES_TO{provenance:[chunk_id…]} · IN_COMMUNITY │
        └───────────────────────────────────────────────────────────┘
```

---

## The one idea

Every entity, relation and summary is traceable to the exact chunks that produced it.

```python
Relation(source="Kestrel Systems", target="Fjord Protocol", type="uses",
         provenance={"kestrel-systems::a1b2…", "fjord-protocol::c3d4…"})
```

Deleting a document is then a graph walk, not a rebuild:

1. Find its chunks.
2. Strip those chunk ids from every relation's `provenance`. **Empty ⇒ the relation had no
   other source and is deleted. Non-empty ⇒ it survives, weakened.**
3. Detach those chunks from every entity's mention set. **Zero mentions ⇒ orphan ⇒ collect.**
4. Mark the affected communities dirty. Nothing else is touched.

Without provenance, deletion is impossible without a full rebuild. With it, deletion is
`O(affected subgraph)`. That single invariant is the project.

**Systems framing:** the index behaves like an **LSM-tree**. Cheap incremental writes patch
locally and mark dirty; a periodic background **compaction** re-runs global clustering to
amortise the drift that local decisions accumulate.

---

## Results

From `make eval` on the bundled offline benchmark (seeded, reproducible, no API key). The
numbers below are one run's `results/report.md`; regenerate with one command.

### Deletion is where incremental indexes fail

| system | stale-answer rate ↓ | survivor recall ↑ | deletion tokens ↓ | churn tokens ↓ |
|---|---|---|---|---|
| **evolving (this)** | **0.00** | **1.00** | **2 705** | **12 281** |
| full-reindex | 0.00 | 1.00 | 22 866 | 52 193 |
| append-only | **1.00** | 1.00 | 0 | 12 787 |
| flat-vector RAG | 0.00 | **0.33** | 0 | 599 |

*Stale-answer rate* = questions still answered with a fact that only a deleted document
supported. *Survivor recall* = facts a **surviving** document still supports that remain
answerable — the over-deletion control, because deleting too much is as wrong as deleting
too little.

Append-only is free and completely wrong after a deletion. Flat vector RAG deletes easily
but cannot answer across documents. This system matches full-reindex on both, for **8.5×
fewer tokens** on the deletion phase and **4.3× fewer** across the churn phase.

### Cost vs freshness is a curve, not a switch

The knob is the *recompute budget*: how many dirty summaries may be regenerated per change.

| configuration | update tokens | stale summaries per query | dirty after |
|---|---|---|---|
| budget = 0 | 5 497 | 2.24 | 71% |
| budget = 1 | 8 601 | 1.41 | 43% |
| budget = 2 | 10 773 | 0.82 | 29% |
| **budget = 4** | **14 986** | **0.00** | **0%** |
| full reindex | 75 059 | 0.00 | 0% |

Full freshness for **5× fewer tokens** than rebuilding — or trade down to **13.7× fewer** if
some staleness is acceptable. → `results/fig_pareto.svg`

Note which freshness measure moves. `stale_answer_rate` is a property of the *graph*: once
provenance GC is correct it is 0 at every budget, because retraction never depended on
summaries. What the budget buys is *reader-visible* staleness — how many out-of-date
community summaries a query actually leans on.

### The asymptotic argument

| corpus | documents | build (rebuild) tokens | one update | rebuild ÷ update |
|---|---|---|---|---|
| ×1 | 5 | 14 995 | 2 491 | 6.0× |
| ×2 | 10 | 33 209 | 2 495 | 13.3× |
| ×4 | 20 | 70 005 | 2 499 | 28.0× |
| ×8 | 40 | 148 122 | 2 503 | **59.2×** |

Update cost is **flat** in corpus size; rebuild cost is linear. The advantage grows without
bound. → `results/fig_scaling.svg`

### Fast path

Synchronous write latency is 0.4–8 ms because handlers never call a model: they persist
intent, run the pure-graph work, mark dirty and enqueue. A local edit reuses the chunks it
did not touch, so an edit costs proportional to the edit, not to the document.

---

## Quick start

### Offline, no daemons, ~30 seconds

```bash
make install          # venv + package
make test             # 107 tests, fully offline
make demo             # ingest → query → update → delete, narrated
make eval             # every table and figure into results/
```

Everything above runs with `EGRAPH_STORE_BACKEND=memory`, `EGRAPH_QUEUE_BACKEND=inline` and
`EGRAPH_LLM_BACKEND=mock` — no Docker, no Redis, no API key, no model download.

### The full stack

```bash
cp .env.example .env
make up               # arcadedb + redis + api + worker + frontend + prometheus + grafana
make seed             # load the mini-corpus
make churn            # drive a live add/update/delete against the running API
```

| service | url |
|---|---|
| console (frontend) | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| ArcadeDB Studio | http://localhost:2480 |

`make scale N=4` scales the workers — that is the throughput knob; the api stays at one.

### Against a real model

```bash
export ANTHROPIC_API_KEY=...          # or: ant auth login
EGRAPH_LLM_BACKEND=anthropic make demo
```

`EGRAPH_LLM_BACKEND=gateway` instead routes every call through your own FastAPI proxy
(`POST /v1/complete`), which is the option to use when the proxy owns provider credentials.

---

## The demo

`make churn` (or the frontend's Documents page) with Grafana open beside it:

1. **Seed** five documents — watch entities, relations and communities appear.
2. **Edit one paragraph** of `northwind` — the response returns in milliseconds and reports
   *chunks reused*; the dirty-communities gauge spikes to 3 of 9 and drains as the worker
   re-summarises **only those three**.
3. **Delete** `northwind` — the response carries the full GC receipt: relations expired,
   relations *weakened* (another document still supports them), entities collected.
4. **Ask the same question again** — the retracted fact is gone, while a fact a surviving
   document supports is still answerable.
5. **`/maintenance/integrity`** — no orphan entities, no unsupported relations, nothing
   citing a chunk that no longer exists.

---

## API

```
POST   /documents            {uri, content}   → ChurnResult (202, returns fast)
POST   /documents/upload     multipart files  → per-file receipts + rejections
PUT    /documents/{doc_id}   {content}        → ChurnResult with chunk reuse counts
DELETE /documents/{doc_id}                    → ChurnResult with the GC receipt
POST   /query                {question, mode} → Answer + citations + staleness touched
GET    /index/staleness                       → {dirty_count, dirty_fraction, oldest_dirty_age_s}
GET    /index/stats  /index/graph  /index/communities  /index/costs  /index/resolutions
POST   /maintenance/recompute  /maintenance/compact
GET    /maintenance/integrity                 → the provenance invariants, checked live
GET    /health  /ready  /config  /metrics
```

Writes return fast; `/query` always answers and reports `stale_communities_touched` so
freshness is visible rather than silently wrong.

Uploads are text-only (`.md`, `.markdown`, `.txt`, `.text`, `.rst`, 2 MB each) and report
rejections per file instead of failing the batch. A PDF would need a document-conversion
stage this project does not have, and ingesting whatever bytes arrive would fill the graph
with entities nobody can trace back to real prose.

---

## Layout

```
src/egraph/
├── schemas/      Pydantic contracts — the spine
├── store/        GraphStore ABC + memory and ArcadeDB backends, provenance-aware ops
├── ingest/       intake, content-addressed chunking, entity resolution ★
├── extract/      LLM extraction + content-hash cache
├── churn/        ★ THE CONTRIBUTION ★ engine · diff · mutations · dirty · gc · compact
├── cluster/      incremental placement (hot path) · Leiden/Louvain (compaction)
├── summarize/    community summaries, recomputed only when dirty
├── retrieve/     local + global + dual retrieval, provenance-cited answers
├── workers/      arq tasks and the queue abstraction
├── api/          FastAPI — thin, fast, never blocks on a model
├── gateway/      the only doorway to a model; mock / anthropic / proxy backends
├── cost/         CostMeter — no unmetered call exists
└── eval/         time-split churn benchmark, baselines, metrics, Pareto sweep
frontend/         React console: churn, query, graph, communities, resolution, maintenance
```

---

## Evaluation

`make eval` runs the **time-split churn benchmark**:

```
t0  base corpus       → build the index, ask base questions
t1  updates + new     → ask base questions again (historical stability)
                        and new questions (did the change land?)
t2  deletions         → ask deletion questions (is retracted info still served?)
                        and survivor questions (was too much deleted?)
```

against four systems — this one, full-reindex, append-only and flat-vector RAG — plus the
Pareto sweep, a compaction ablation, an entity-resolution threshold sweep and a scaling
study. Output: `summary.json`, five CSVs, three SVG figures and `report.md`.

Three things that make the benchmark trustworthy rather than merely favourable, all
asserted by tests:

- **A seeded run is bit-reproducible**, across processes, for every metric except wall
  clock. Getting there caught a real bug: the recompute queue was ordered by `dirty_since`,
  and two communities marked in the same clock tick tie — so oldest-first alternated
  between time order and insertion order, and under a bounded budget that changed *which*
  summaries were regenerated. The ordering key is now an explicit counter.
- **Every `must_exclude` fact is uniquely supported by a deleted document.** A fact a
  surviving document also states would score a correct system as stale.
- **The full-reindex baseline does not get this project's extraction cache.** Crediting it
  with a content-hash cache would credit it with the contribution under evaluation.

---

## Honest limitations

- **The `mock` backend caps answer quality.** It is a genuine rule-based extractor and
  extractive reader, which is what makes CI possible, but token-F1 against a reference
  sentence is depressed by it and base recall sits at 0.83. The *maintenance* metrics —
  stale-answer rate, update cost, communities recomputed — are backend-independent, and
  those are the project's claims. Run with `EGRAPH_LLM_BACKEND=anthropic` for quality
  numbers.
- **ArcadeDB's HNSW index is not reachable from SQL**, so entity vector search loads
  embeddings into an in-process matrix and scores with numpy, refreshing when the entity
  generation changes. At this project's corpus sizes that is microseconds and exactly
  reproducible; `store/vectors.py::VectorIndex` is the single swap-in point for a real ANN
  index.
- **The corpus is small and synthetic.** It is engineered so the invariants are *checkable*
  (facts with exactly one supporter and facts with two), not to be representative. The
  scaling study replicates it to 8× to show the asymptotics, but that is replication, not
  diversity.
- **Single-node only.** Production would need workers on k8s with HPA on queue depth,
  ArcadeDB replication, and communities sharded across worker pools. Out of scope here.

---

## Configuration

Every knob lives in `src/egraph/settings.py` and is settable by `EGRAPH_*` environment
variable; `.env.example` documents the full set. The ones that matter:

| variable | default | what it does |
|---|---|---|
| `EGRAPH_DIRTY_BATCH_SIZE` | 8 | summaries recomputed per worker tick — the Pareto knob |
| `EGRAPH_COMPACTION_INTERVAL_S` | 900 | how often global re-clustering runs |
| `EGRAPH_TAU_SIM` / `TAU_NAME` | 0.86 / 0.90 | entity merge thresholds; **both** must clear |
| `EGRAPH_CHUNK_SIZE` / `CHUNK_OVERLAP` | 400 / 0 | overlap > 0 destroys chunk reuse — see CLAUDE.md |
| `EGRAPH_MAX_COMMUNITY_SIZE` | 8 | when incremental placement seeds a new community |

Every eval run is seeded and stamped with a `config_hash` derived from the whole settings
object, so a figure is always traceable to the configuration that produced it.

---

## Licence

MIT.
