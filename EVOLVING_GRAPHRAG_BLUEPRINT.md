# Evolving GraphRAG — Architecture & Deployment Blueprint

> The design document of record for `evolving-graphrag`. §1–§17 are the specification.
> §18 records where the implementation deviated from it and why — read that before
> assuming a section describes the code as it stands.
>
> A knowledge-graph RAG service that maintains its entity/relation graph and community
> summaries **incrementally under document churn** (add / update / delete), tracks index
> staleness, and proves a **cost-vs-freshness** trade-off against full-reindex and
> append-only baselines.
>
> **The contribution is the maintenance layer, not RAG answer quality.**

---

## 1. System overview

```
                      ┌──────────────────── FastAPI API ────────────────────┐
                      │ POST /documents  PUT /documents/{id}  DELETE /{id}   │
                      │ POST /query      GET /index/staleness   GET /health  │
                      └───────┬─────────────────────────────────┬───────────┘
                              │ fast: write intent + mark dirty │ query path
                              ▼                                 ▼
                    ┌──────────────────┐              ┌───────────────────┐
                    │  CHURN ENGINE    │              │  RETRIEVAL ENGINE │
                    │  diff → mutate → │              │  dual-level:      │
                    │  mark dirty      │              │  local entity +   │
                    └───┬──────────┬───┘              │  global community │
                        │          │ enqueue          └─────────┬─────────┘
                        │          ▼                            │
                        │   ┌──────────────┐   ┌──────────────┐ │
                        │   │ JOB QUEUE    │──▶│  WORKERS     │ │
                        │   │ (Redis/arq)  │   │ extract·embed│ │
                        │   └──────────────┘   │ ·summarize   │ │
                        │                      │ ·compact     │ │
                        ▼                      └──────┬───────┘ │
                 ┌─────────────────────────────────────────────┐│
                 │           ARCADEDB (graph + vector)          │◀┘
                 │  SourceDocument · Chunk · Entity · Community │
                 │  MENTIONS · RELATES_TO · IN_COMMUNITY        │
                 └─────────────────────┬───────────────────────┘
                                       │
                 ┌─────────────────────▼───────────────────────┐
                 │  LLM GATEWAY — cost accounting + cache       │
                 ├─────────────────────────────────────────────┤
                 │  OBSERVABILITY (Prometheus + Grafana)        │
                 │  dirty-community gauge · latency · tokens    │
                 ├─────────────────────────────────────────────┤
                 │  EVAL HARNESS — time-split churn benchmark   │
                 │  baselines · cost/freshness Pareto · metrics │
                 └─────────────────────────────────────────────┘
```

**The write path is asynchronous.** A mutation writes intent and marks affected communities
dirty **synchronously** (fast response); the expensive LLM work runs in **background
workers**. That split is what makes the system live-demoable and the incremental cost
measurable.

---

## 2. The core design principle — provenance-first

Every entity, relation and community summary must be traceable to the exact chunks that
produced it. This is the enabling invariant for churn:

- A **relation** stores the set of chunk ids that support it (`provenance: [chunk_id, …]`).
  On deletion you remove those chunk ids; when the set empties, the relation is collected.
- An **entity** persists while any chunk still mentions it; when its last `MENTIONS` edge is
  removed it is an orphan and gets collected.
- A **community summary** records its `summary_hash` and the member entities it covered;
  when any member is mutated the community is marked **dirty** and re-summarised — *only
  that community*.

Without provenance, deletion is impossible without a full rebuild. With it, deletion is a
graph walk. That is the whole project in one invariant.

**Systems framing:** treat the index like an **LSM-tree** — cheap incremental writes
(mutations mark dirty and patch locally) plus a periodic background **compaction** (full
re-clustering) that amortises drift. This mirrors how the field avoids full Leiden
recomputation: new entities are placed into existing communities without triggering a global
recompute, with periodic global reclustering.

---

## 3. Tech stack

- **Runtime:** Python 3.11+, `pyproject.toml`.
- **API:** FastAPI (async), Pydantic v2 for all contracts, `pydantic-settings` for config.
- **Graph + vector store:** **ArcadeDB** (graph vertices/edges + vectors in one store).
- **Queue / workers:** **Redis** + **arq**. Redis also serves as the extraction cache.
- **Community detection:** Leiden (`igraph`/`leidenalg`) for compaction; incremental
  placement on the fast path.
- **LLM gateway:** a FastAPI proxy or the Anthropic SDK — extraction and summarisation,
  with cost/token accounting and content-hash response caching.
- **Embeddings:** a local sentence-transformer, or a deterministic hashed projection.
- **Observability:** `prometheus-client` + Grafana.
- **Deploy:** Docker Compose (single-node, dev + demo). k8s/Helm noted as future work.
- **Testing:** pytest + a tiny offline corpus fixture that runs the full churn cycle
  without a GPU.

---

## 4. Repository layout

See the README for the layout as built. The blueprint's structure is followed with the
deviations noted in §18.

---

## 5. Graph schema (ArcadeDB)

**Vertices**

```
SourceDocument { doc_id, uri, content_hash, version, status: active|deleted,
                 ingested_at, updated_at }
Chunk          { chunk_id, doc_id, text, position, embedding[], content_hash }
Entity         { entity_id, canonical_name, type, description, embedding[], degree,
                 community_id, created_at, updated_at }
Community      { community_id, level, title, summary, summary_hash,
                 dirty: bool, member_count, updated_at }
```

**Edges**

```
PART_OF      (Chunk  → SourceDocument)
MENTIONS     (Chunk  → Entity)   { confidence }
RELATES_TO   (Entity → Entity)   { relation_type, description, weight,
                                   provenance: [chunk_id…], updated_at }   ★
IN_COMMUNITY (Entity → Community)
```

`RELATES_TO.provenance` is the retraction lever. Deleting a document → find its chunks →
for every `RELATES_TO` citing those chunk ids, remove them from `provenance`; if the set
empties, delete the relation; then any entity whose `MENTIONS` set is now empty is an
orphan → collect; then mark the communities of all touched entities **dirty**.

Index requirements: unique index on `content_hash` (idempotent ingest), vector index on
`Entity.embedding` and `Chunk.embedding`, index on `Community.dirty` (fast dirty scan).

The full DDL lives in `config/graph_schema.sql`.

---

## 6. Core data contracts

```python
class DocumentChange(BaseModel):
    op: Literal["add", "update", "delete"]
    doc_id: str
    uri: str | None = None
    content: str | None = None            # None for delete
    content_hash: str | None = None

class GraphMutation(BaseModel):
    kind: Literal["add_entity","remove_entity","add_relation","expire_relation","update_entity"]
    payload: dict
    provenance: list[str]                 # chunk ids
    affected_communities: list[str]       # to be marked dirty

class Community(BaseModel):
    community_id: str; level: int; title: str; summary: str
    summary_hash: str; dirty: bool; member_count: int

class Answer(BaseModel):
    text: str
    citations: list[str]                  # source doc/chunk ids (provenance)
    used_communities: list[str]
    stale_communities_touched: int        # freshness signal in the response
```

---

## 7. Module contracts

**ChurnEngine** — the front door for every write:

```python
class ChurnEngine:
    def apply(self, change: DocumentChange) -> ChurnResult:
        # add:    chunk → extract → resolve → upsert with provenance → mark dirty
        # update: re-chunk → diff → minimal mutations → mark dirty
        # delete: walk provenance → strip chunk ids → gc orphans → mark dirty
        # SYNC part returns fast; extraction/summary enqueued to workers
```

**EntityResolver** — dedup/merge, made explicit because it is GraphRAG's known weak point:

```python
class EntityResolver:
    def resolve(self, candidate: Entity) -> tuple[str, Literal["merge","create"]]:
        # embedding cosine >= tau_sim AND normalised-name match >= tau_name → merge
        # else create. Record the decision for eval.
```

**DirtyTracker** — `mark(community_ids)` and `staleness() -> {dirty_fraction,
oldest_dirty_age, dirty_count}`.

**SummaryRecomputer** — `recompute_dirty(batch_size) -> int`: pull dirty communities,
regenerate, clear the flag, return the count.

**Retriever** — local (entity vector + 1-hop) ⊕ global (community summaries) → context →
answer generation with provenance citations and `stale_communities_touched`.

---

## 8. The churn engine in detail

**Document-level API decomposes into five graph mutations:**

- **add_document** → chunk → embed → extract → resolve (merge/create) → upsert with
  provenance → mark member communities dirty → enqueue summary recompute.
- **update_document** → re-chunk + re-extract → diff old-vs-new → emit only the delta →
  mark dirty. *Never re-process unchanged chunks* — the key to the cost win.
- **delete_document** → mark `status = deleted` → walk `PART_OF`/`MENTIONS`/`RELATES_TO` →
  strip chunk ids from every relation's `provenance` → GC empty relations and orphan
  entities → mark affected communities dirty.

**Selective recompute (the cost lever):** only communities with ≥1 dirty member are
re-summarised, in batches, by a worker. Track `communities_recomputed` per change.

**Compaction (the drift lever):** a scheduled worker re-runs full clustering and
re-summarises changed communities — amortised, off the hot path. The interval is a config
knob and the eval measures quality with and without it.

**Idempotency:** `content_hash` on documents and chunks makes re-ingesting an unchanged
document a no-op — a correctness property, and tested as one.

**Staleness contract:** `GET /index/staleness` returns `{dirty_fraction, dirty_count,
oldest_dirty_age}`. Queries always answer, but the `Answer` reports
`stale_communities_touched` so freshness is visible, never silently wrong.

---

## 9. Cost accounting + evaluation

**CostMeter** wraps every LLM/embedding call → records `{operation, tokens, wall_ms, usd}`.
No result exists unless it is metered; the Pareto curve depends on it.

**Time-split churn benchmark:** base corpus (older docs) + new corpus (recent docs) + a
**deletion set** (docs whose facts are directly queried). Scenarios: base-queries/base-corpus,
base-queries/updated-corpus (historical stability), new-queries/updated (new-info recall),
and **deletion** (facts that should disappear).

**Baselines:** full-reindex-every-change (the naive production default), append-only
incremental (handles adds, ignores deletes), flat vector RAG.

**Metrics:** update cost (tokens + wall-clock), **stale-answer rate after deletions**,
answer quality retained, memory growth vs corpus size, communities recomputed per change.
The Pareto sweep varies the recompute budget / compaction interval → the headline figure.

---

## 10. API surface

```
POST   /documents           {uri, content}     → {doc_id, status:"queued"}
PUT    /documents/{doc_id}  {content}          → {doc_id, mutations, status}
DELETE /documents/{doc_id}                     → {doc_id, gc_summary, status}
POST   /query               {question, mode}   → Answer (provenance + freshness)
GET    /index/staleness                        → Staleness
GET    /index/stats                            → {entities, relations, communities, dirty}
GET    /health  /ready                         → liveness / readiness
```

---

## 11. Deployment architecture

### 11.1 Topology (Docker Compose, single node)

```
api        FastAPI (uvicorn)  :8000   → depends on arcadedb, redis
worker     arq worker(s)              → consumes redis queue   (scale: replicas=N)
frontend   React console      :5173   → same-origin proxy to api
arcadedb   graph + vector     :2480   (volume: arcadedb-data)
redis      queue + cache      :6379   (volume: redis-data)
prometheus scrapes api+worker :9090   (volume: prom-data)
grafana    dashboards         :3000   (provisioned)
```

### 11.2 Why each service exists

- **api** — thin, synchronous, fast. Validates, writes intent, marks dirty, enqueues. Never
  blocks on an LLM call.
- **worker** — where cost lives. **Scaling worker replicas is the throughput knob**; that is
  the answer to "how does this scale".
- **arcadedb** — a single multi-model store, so there is no separate vector DB to operate.
- **redis** — durable-enough queue plus the content-hash extraction cache, which is itself a
  measurable cost result.
- **prometheus + grafana** — the dirty-community gauge, update-latency histogram, token
  counter and index-size gauges. The demo and the report both read from here.

### 11.3 Config, health, operations

- **Config** via `pydantic-settings` + `.env`: models, `TAU_SIM`, `DIRTY_BATCH_SIZE`,
  `COMPACTION_INTERVAL`, gateway URL, DSNs. Nothing hard-coded.
- **Health/readiness:** `/health` (process up) and `/ready` (store + queue reachable, schema
  migrated). Compose healthchecks gate startup order.
- **Persistence:** named volumes for `arcadedb-data` and `redis-data`; `make backup` before
  a demo.
- **Idempotency & backpressure:** `content_hash` dedup makes retries safe; queue depth is a
  Prometheus gauge and enqueue is capped so a bulk ingest cannot melt the workers.
- **Reproducibility:** every eval run is seeded and stamped with a `config_hash`;
  `make eval` regenerates all figures from scratch.
- **Secrets:** API keys only via env, never committed.
- **One-command bring-up:** `make up`, then `make demo`.

### 11.4 Scaling path (future work, not built)

Move workers to a k8s Deployment with HPA on queue depth, run ArcadeDB with replication, and
shard communities across worker pools. Stated as future work; building it is out of scope.

---

## 12. Observability signals

| Signal | Type | Why it matters |
|---|---|---|
| `dirty_communities` | gauge | live staleness — the demo watches it spike then drain |
| `update_latency_ms` | histogram | sync write speed (the fast-path claim) |
| `summary_recompute_total` | counter | proves *selective* recompute (few, not all) |
| `llm_tokens_total{op}` | counter | the cost axis of the Pareto |
| `extraction_cache_hits` | counter | a measurable cost win |
| `index_entities/relations/communities` | gauge | memory-growth-vs-corpus figure |

---

## 13. Phased build plan

**P0 — Schema + provenance + offline cycle.** *Accept:* ingest 5 docs, query, get an answer
with correct provenance citations; `test_provenance` green.

**P1 — Full churn engine + GC + dirty tracking.** *Accept:* deleting a document removes
**exactly** the subgraph it uniquely supported (property test), orphans collected, correct
communities dirtied, nothing unrelated changed.

**P2 — Async workers + selective recompute + API.** *Accept:* `POST /documents` returns
fast; a worker recomputes *only* dirty communities; `DELETE` drops stale facts;
`/index/staleness` reflects reality.

**P3 — Compaction + resolution quality + retrieval polish.** *Accept:* compaction
re-clusters and re-summarises only changed communities; retrieval uses local + global;
resolution decisions are queryable.

**P4 — Eval harness + Pareto + observability.** *Accept:* one command reproduces the
cost-vs-freshness Pareto and the deletion stale-answer-rate table.

**P5 — Deployment hardening + demo.** *Accept:* `make up` brings up the whole stack; a live
add/delete moves the `dirty_communities` gauge while only affected summaries recompute.

---

## 16. Risk register

| Risk | Mitigation (built into this design) |
|---|---|
| Entity resolution errors corrupt the graph | Explicit `EntityResolver` with logged decisions + threshold sweep in eval |
| Incremental drift degrades community quality | Periodic compaction (LSM-style) + quality-vs-compaction ablation |
| Deletion misses stale facts | Provenance-set GC + a deletion benchmark measuring stale-answer rate |
| ArcadeDB vector/graph perf unknown | Validated early; flat-vector baseline is the fallback store |
| LLM extraction cost blows up | Content-hash extraction cache (measured) + local embeddings + mockable LLM |
| "Just another RAG project" | Contribution is the maintenance layer + cost model, not QA quality |

---

## 17. What this buys at each review

- **Review 1:** architecture + deployment topology, graph schema, baselines, metrics and the
  time-split benchmark protocol locked, risk register.
- **Review 2:** a live demo — add/delete a document, watch selective recompute and the
  staleness gauge — plus real cost numbers and a started deletion comparison.
- **Final:** the cost-vs-freshness Pareto, the deletion stale-answer-rate table beating
  append-only at full-reindex freshness, a hardened one-command deployment, and a viva story
  rooted in a clean systems invariant.

---

## 18. Implementation notes — where the build deviated, and why

Recorded here so the spec and the code do not silently disagree.

1. **Descriptions are provenance-tracked too.** §2 covers entities, relations and summaries.
   Building it surfaced a fourth carrier of derived state: the `description` string on an
   entity or relation is prose the model wrote *out of a particular chunk*. An entity that
   survives a deletion was keeping a sentence copied from the deleted document — a real
   provenance leak, found by the deletion scenario. Both now store
   `descriptions: {chunk_id: text}` and re-derive the visible description whenever mentions
   or provenance change.

2. **Chunk ids are position-independent.** The blueprint's chunk identity did not specify
   this. Including a position in the id means inserting one paragraph renumbers every chunk
   below it, so update cost scales with document size rather than edit size — defeating the
   central cost claim. Ids are now `doc_id::hash(text)` with an occurrence counter for
   verbatim repeats, and `chunk_overlap` defaults to **0** because overlapping chunks copy
   their predecessor's tail and destroy the same property.

3. **A `memory` store backend exists alongside ArcadeDB.** §3 specifies a single store. The
   tests, the eval harness and the laptop demo run against an in-process implementation of
   the same `GraphStore` interface, which is what makes the suite runnable with no daemon
   and the eval deterministic. ArcadeDB remains the deployed store; the memory backend is
   the semantics oracle both must satisfy.

4. **ArcadeDB vector search is brute-force over an in-process matrix.** Its HNSW index is
   reachable from the Java API, not from SQL. Embeddings are loaded over SQL into a numpy
   matrix, refreshed when the entity generation changes. This is the risk register's
   "ArcadeDB vector perf unknown" line, resolved by measurement rather than by hope;
   `VectorIndex` is the single swap-in point for a real ANN index.

5. **Compaction falls back to Louvain, not label propagation.** `leidenalg` is optional, so
   a fallback is required for CI and slim containers. Label propagation was tried first and
   collapses a small dense graph into one community — compaction driven by that would
   *destroy* the structure incremental placement built, which is worse than not compacting.
   A pure-Python Louvain is the fallback; label propagation is retained for the ablation.

6. **The `mock` LLM backend is a real extractor, not a stub.** It is a rule-based
   entity/relation extractor, a template summariser and an extractive reader that abstains
   below a relevance floor. Making it real is what lets the maintenance claims be proved in
   CI without an API key. It caps answer quality — see the README's limitations.

7. **Compaction reconciles rather than rebuilds.** §8 says compaction "re-summarises changed
   communities" without saying how they are identified. New clusters are matched to existing
   communities by membership overlap, so a community whose membership did not change keeps
   its id, its summary and its clean flag. A naive implementation would re-summarise
   everything and cost exactly as much as a full reindex.

8. **DELETE is synchronous by design.** §1 makes the whole write path async. Retraction needs
   no model call, so the provenance walk completes in the request and the response carries
   the full GC receipt. This is both the demo and the argument, and it is the only handler
   permitted to do real work inline.

9. **The Pareto's freshness axis is reader-visible staleness.** Stale-answer rate turned out
   to be a property of the *graph* — once GC is correct it is 0 at every recompute budget,
   so it does not trace a curve. The sweep therefore plots mean *stale summaries touched per
   query*, which is what the recompute budget actually buys. Stale-answer rate is still
   reported, as the deletion-correctness result.

10. **A `deletion-survivors` scenario was added.** §9's four scenarios can all be passed by a
    system that deletes too much. The fifth asks for facts a *surviving* document still
    supports and requires them to remain answerable.

11. **Not built:** §15's session-prompt list (an authoring aid, not a system component), and
    §11.4's k8s path, which remains stated future work.
