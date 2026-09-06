# Deploying evolving-graphrag

There are two deployment shapes, and the difference between them matters more than the
choice of host.

| | what it proves | services | cost |
|---|---|---|---|
| **demo-lite** | the system works; a public URL to click around | 1 container + Postgres | free |
| **full stack** | the *architecture* — async writes, worker scaling, the dirty gauge draining | 6 services + a disk | ~$5–20/mo, or a VPS |

**Demo-lite is honestly lossy.** With no worker there is no queue, so extraction runs
inline on the request and the asynchronous write path — the thing this project is about —
is never exercised. Everything else is real: Postgres persistence, the full provenance and
GC semantics, selective recompute, and every invariant the test suite checks.

Use demo-lite for a link you can send someone. Use the full stack for a review demo.

---

## Demo-lite

One container serving the API *and* the console, plus a managed Postgres. The API is
mounted twice — at the root for API clients and under `/api` for the console, whose own
`/documents` and `/query` routes would otherwise shadow the API's.

The image is the last stage of `Dockerfile`, so a plain `docker build .` produces it, which
is what every PaaS does when given no target.

### Render

```bash
# Render dashboard → New → Blueprint → connect this repo → Apply
```

`render.yaml` declares the web service and a free Postgres and wires the connection string
across. Nothing else to configure.

Free-tier caveats: the instance sleeps after inactivity and cold-starts on the next request
(~30 s), and Render's free Postgres expires after 30 days. For anything long-lived, point
`EGRAPH_POSTGRES_DSN` at Neon or Supabase instead — both have durable free tiers.

### Railway

New Project → Deploy from GitHub → add a Postgres plugin. Railway detects the Dockerfile.
Set:

```
EGRAPH_STORE_BACKEND=postgres
EGRAPH_POSTGRES_DSN=${{Postgres.DATABASE_URL}}
EGRAPH_QUEUE_BACKEND=inline
EGRAPH_LLM_BACKEND=mock
EGRAPH_SEED_ON_START=true
```

### Fly.io

```bash
fly launch --no-deploy              # generates fly.toml; keep the Dockerfile
fly postgres create --name egraph-db
fly postgres attach egraph-db       # sets DATABASE_URL
fly secrets set EGRAPH_STORE_BACKEND=postgres EGRAPH_QUEUE_BACKEND=inline \
                EGRAPH_LLM_BACKEND=mock EGRAPH_SEED_ON_START=true
fly secrets set EGRAPH_POSTGRES_DSN="$(fly ssh console -C 'echo $DATABASE_URL')"
fly deploy
```

### Hugging Face Spaces

Create a Docker Space, push this repo, and add to the Space README frontmatter:

```yaml
sdk: docker
app_port: 8000
```

Spaces have no managed Postgres; either point `EGRAPH_POSTGRES_DSN` at Neon, or set
`EGRAPH_STORE_BACKEND=memory` and accept that the index resets when the Space restarts.

### Running it locally exactly as deployed

```bash
docker build --target demo -t egraph-demo .
docker run --rm -p 8090:8090 -e PORT=8090 \
  -e EGRAPH_POSTGRES_DSN="postgresql://egraph:egraph@host.docker.internal:5432/egraph" \
  egraph-demo
```

---

## Full stack

Every service, including Prometheus and Grafana. The simplest host is a small VPS, because
`docker-compose.yml` already describes the whole thing:

```bash
# Hetzner CX22 (~€4/mo) or Oracle Cloud Always Free
git clone https://github.com/Hrithik373/evolving-graphrag && cd evolving-graphrag
cp .env.example .env      # set ARCADEDB_PASSWORD, GRAFANA_PASSWORD, ANTHROPIC_API_KEY
make up
make seed
make scale N=4            # the throughput knob
```

Then put a reverse proxy with TLS in front of ports 8000 (api), 5173 (console) and 3000
(Grafana), and **do not** expose 2480 (ArcadeDB) or 6379 (Redis) publicly.

On a PaaS the same topology needs: a web service (api), a background worker, a private
service with a persistent volume (ArcadeDB), and Redis. Render, Fly and Railway can all do
it; none of them can do it for free, because background workers and persistent disks are
paid everywhere.

---

## Choosing a store backend

| backend | when | persistence |
|---|---|---|
| `memory` | tests, the eval harness, a laptop demo | none (optional JSON snapshot) |
| `postgres` | any deployment | managed and free nearly everywhere |
| `arcadedb` | the full stack, and the graph-native story | needs Docker + a volume |

All three implement the same `GraphStore` interface and are held to the same behaviour by
`tests/test_store_parity.py`, which runs every test once per backend. The memory store is
the semantics oracle; the others must match it exactly.

Postgres is the deployable one, and it is not a compromise. The central operation of the
whole system — "which relations does this chunk support?" — is an array-containment query
against a **GIN index**, so retraction is an index scan. The graph backend answers the same
question through a collection predicate whose support varies by version and whose fallback
is a client-side scan of every relation.

To run the parity suite against real servers:

```bash
docker compose up -d arcadedb postgres
EGRAPH_ARCADEDB_URL=http://localhost:2480 \
EGRAPH_TEST_POSTGRES_DSN=postgresql://egraph:egraph@localhost:5432/egraph \
  pytest tests/test_store_parity.py -v
```

---

## Configuration

Everything is an `EGRAPH_*` environment variable; `.env.example` documents the full set.
The ones that matter for a deployment:

| variable | notes |
|---|---|
| `PORT` | set by the platform; the container binds whatever it is given |
| `EGRAPH_STORE_BACKEND` | `memory` \| `postgres` \| `arcadedb` |
| `EGRAPH_POSTGRES_DSN` | most providers hand you this as `DATABASE_URL` |
| `EGRAPH_QUEUE_BACKEND` | `inline` with no worker, `arq` with one |
| `EGRAPH_LLM_BACKEND` | `mock` (free, deterministic), `anthropic`, or `gateway` |
| `EGRAPH_SEED_ON_START` | loads the mini-corpus **only into an empty index** |
| `ANTHROPIC_API_KEY` | only when `EGRAPH_LLM_BACKEND=anthropic` |

`EGRAPH_SEED_ON_START` is guarded on the index being empty, not on the flag alone, so a
redeploy can never overwrite documents somebody uploaded.

---

## Security before you make it public

The service has **no authentication**. Anyone with the URL can upload, delete and query.
That is fine for a demo you control and wrong for anything else. Before exposing it:

- put it behind your platform's access control, or a reverse proxy with basic auth;
- keep `ANTHROPIC_API_KEY` in the platform's secret store, never in the repo;
- with `EGRAPH_LLM_BACKEND=anthropic`, remember that every upload spends tokens — an open
  URL is an open wallet. `mock` costs nothing and is the safer public default;
- narrow `EGRAPH_CORS_ORIGINS` from `["*"]` to the console's origin if you split them;
- never expose ArcadeDB (2480) or Redis (6379) — neither is authenticated by default here.
