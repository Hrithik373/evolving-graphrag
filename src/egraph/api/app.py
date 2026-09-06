"""FastAPI surface.

Design rule enforced here: **a handler never blocks on a model call.** Writes persist
intent, run the pure-graph part of the change (provenance GC), mark communities dirty and
enqueue the rest. The only exception is by design - DELETE completes its provenance walk
synchronously, because retraction needs no model and returning the GC receipt in the
response is the whole demonstration.

Handlers run the store in a worker thread (``run_in_threadpool``) because the store client
is synchronous; that keeps the event loop free without pretending the driver is async.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from egraph import __version__
from egraph.api.deps import get_pipeline
from egraph.churn.gc import dangling_relations
from egraph.ingest.loader import make_doc_id
from egraph.observability import metrics
from egraph.pipeline import Pipeline
from egraph.schemas import (
    Answer,
    ChurnResult,
    DocumentChange,
    IndexStats,
    QueryMode,
    QueryRequest,
    Staleness,
)
from egraph.settings import get_settings
from egraph.workers.queue import QueueFullError

log = logging.getLogger(__name__)

# Uploads are text-only on purpose. Extracting a knowledge graph from a PDF or a .docx
# needs a document-conversion stage this project does not have, and silently ingesting
# whatever bytes arrive would fill the graph with entities nobody can trace to real prose.
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".rst"}
MAX_UPLOAD_BYTES = 2_000_000


# ---------------------------------------------------------------------- request bodies
class CreateDocument(BaseModel):
    uri: str
    content: str
    doc_id: str | None = None


class UpdateDocument(BaseModel):
    content: str
    uri: str | None = None


class AskQuestion(BaseModel):
    question: str
    mode: QueryMode = "dual"
    top_k_entities: int = Field(default=8, ge=1, le=50)
    top_k_communities: int = Field(default=3, ge=0, le=20)
    max_hops: int = Field(default=1, ge=0, le=3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pipeline = get_pipeline()
    if get_settings().seed_on_start:
        await run_in_threadpool(_seed_if_empty, pipeline)
    metrics.observe_index(pipeline.store.stats())
    log.info("api ready v%s", __version__)
    yield
    pipeline.close()


def _seed_if_empty(pipeline: Pipeline) -> None:
    """Load the bundled mini-corpus, but only into an index that has nothing in it.

    Guarded on emptiness rather than on a flag alone: a restart of a deployed service must
    never re-seed over documents somebody uploaded.
    """
    if pipeline.store.list_documents():
        return
    try:
        from fixtures.mini_corpus import BASE_DOCS
    except ImportError:
        log.warning("seed_on_start is set but the fixture corpus is not installed")
        return
    for doc_id, text in BASE_DOCS.items():
        pipeline.apply(DocumentChange(op="add", doc_id=doc_id, uri=f"{doc_id}.md", content=text))
    report = pipeline.drain()
    log.info("seeded %d documents, %d summaries written", len(BASE_DOCS), report.recomputed)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="evolving-graphrag",
        version=__version__,
        summary="Incrementally-maintained knowledge-graph RAG with provenance-first churn.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    router = APIRouter()

    # ------------------------------------------------------------------ documents
    @router.post("/documents", response_model=ChurnResult, status_code=202, tags=["documents"])
    async def add_document(
        body: CreateDocument, pipeline: Pipeline = Depends(get_pipeline)
    ) -> ChurnResult:
        change = DocumentChange(
            op="add",
            doc_id=body.doc_id or make_doc_id(body.uri),
            uri=body.uri,
            content=body.content,
        )
        return await _apply(pipeline, change)

    @router.post("/documents/upload", status_code=202, tags=["documents"])
    async def upload_documents(
        files: list[UploadFile] = File(...),
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> dict[str, Any]:
        """Ingest uploaded text files, one document each.

        Rejections are reported per file rather than failing the whole batch: dropping a
        folder in and being told which three files were unsupported is more useful than a
        single 415 with nothing ingested.
        """
        results: list[ChurnResult] = []
        rejected: list[dict[str, str]] = []

        for upload in files:
            name = upload.filename or "untitled"
            suffix = Path(name).suffix.lower()
            if suffix not in TEXT_SUFFIXES:
                rejected.append(
                    {
                        "filename": name,
                        "reason": f"unsupported type '{suffix or 'none'}' "
                        f"(accepted: {', '.join(sorted(TEXT_SUFFIXES))})",
                    }
                )
                continue

            raw = await upload.read()
            if len(raw) > MAX_UPLOAD_BYTES:
                rejected.append(
                    {
                        "filename": name,
                        "reason": f"{len(raw) / 1_000_000:.1f} MB exceeds the "
                        f"{MAX_UPLOAD_BYTES // 1_000_000} MB limit",
                    }
                )
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # A .txt that is not UTF-8 is almost always a binary file with the wrong
                # extension; ingesting its mojibake would poison the graph quietly.
                rejected.append({"filename": name, "reason": "not valid UTF-8 text"})
                continue

            if not text.strip():
                rejected.append({"filename": name, "reason": "file is empty"})
                continue

            change = DocumentChange(op="add", doc_id=make_doc_id(name), uri=name, content=text)
            results.append(await _apply(pipeline, change))

        return {
            "ingested": len(results),
            "rejected": len(rejected),
            "results": [r.model_dump(mode="json") for r in results],
            "rejections": rejected,
        }

    @router.put("/documents/{doc_id}", response_model=ChurnResult, tags=["documents"])
    async def update_document(
        doc_id: str, body: UpdateDocument, pipeline: Pipeline = Depends(get_pipeline)
    ) -> ChurnResult:
        existing = await run_in_threadpool(pipeline.store.get_document, doc_id)
        change = DocumentChange(
            op="update" if existing is not None else "add",
            doc_id=doc_id,
            uri=body.uri or (existing.uri if existing else doc_id),
            content=body.content,
        )
        return await _apply(pipeline, change)

    @router.delete("/documents/{doc_id}", response_model=ChurnResult, tags=["documents"])
    async def delete_document(
        doc_id: str, pipeline: Pipeline = Depends(get_pipeline)
    ) -> ChurnResult:
        existing = await run_in_threadpool(pipeline.store.get_document, doc_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"unknown document {doc_id}")
        return await _apply(pipeline, DocumentChange(op="delete", doc_id=doc_id))

    @router.get("/documents", tags=["documents"])
    async def list_documents(
        status: str | None = Query(default=None, pattern="^(active|deleted)$"),
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> list[dict[str, Any]]:
        docs = await run_in_threadpool(pipeline.store.list_documents, status)
        return [
            {
                "doc_id": d.doc_id,
                "uri": d.uri,
                "status": d.status,
                "version": d.version,
                "content_hash": d.content_hash,
                "updated_at": d.updated_at,
                "chunks": len(pipeline.store.chunks_for_document(d.doc_id)),
            }
            for d in docs
        ]

    @router.get("/documents/{doc_id}", tags=["documents"])
    async def get_document(doc_id: str, pipeline: Pipeline = Depends(get_pipeline)) -> dict:
        doc = await run_in_threadpool(pipeline.store.get_document, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"unknown document {doc_id}")
        chunks = await run_in_threadpool(pipeline.store.chunks_for_document, doc_id)
        return {
            **doc.model_dump(mode="json"),
            "chunks": [
                {"chunk_id": c.chunk_id, "position": c.position, "text": c.text} for c in chunks
            ],
        }

    # ------------------------------------------------------------------ query
    @router.post("/query", response_model=Answer, tags=["query"])
    async def query(body: AskQuestion, pipeline: Pipeline = Depends(get_pipeline)) -> Answer:
        request = QueryRequest(**body.model_dump())
        return await run_in_threadpool(pipeline.query, request)

    # ------------------------------------------------------------------ index
    @router.get("/index/staleness", response_model=Staleness, tags=["index"])
    async def staleness(pipeline: Pipeline = Depends(get_pipeline)) -> Staleness:
        return await run_in_threadpool(pipeline.staleness)

    @router.get("/index/stats", response_model=IndexStats, tags=["index"])
    async def stats(pipeline: Pipeline = Depends(get_pipeline)) -> IndexStats:
        return await run_in_threadpool(pipeline.stats)

    @router.get("/index/graph", tags=["index"])
    async def graph(
        limit: int = Query(default=250, ge=1, le=2000),
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> dict[str, Any]:
        """Node/edge projection for the frontend graph explorer."""
        return await run_in_threadpool(_graph_projection, pipeline, limit)

    @router.get("/index/communities", tags=["index"])
    async def communities(pipeline: Pipeline = Depends(get_pipeline)) -> list[dict[str, Any]]:
        items = await run_in_threadpool(pipeline.store.all_communities)
        return [
            {
                "community_id": c.community_id,
                "title": c.title,
                "summary": c.summary,
                "dirty": c.dirty,
                "member_count": c.member_count,
                "members": sorted(c.members),
                "updated_at": c.updated_at,
                "dirty_since": c.dirty_since,
                # Membership that drifted away from what the standing summary covered.
                "drifted_members": sorted(set(c.members) ^ set(c.summarized_members)),
            }
            for c in sorted(items, key=lambda c: (not c.dirty, c.title))
        ]

    @router.get("/index/resolutions", tags=["index"])
    async def resolutions(
        limit: int = Query(default=100, ge=1, le=1000),
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> list[dict[str, Any]]:
        rows = await run_in_threadpool(pipeline.store.list_resolutions, limit)
        return [r.model_dump(mode="json") for r in reversed(rows)]

    @router.get("/index/costs", tags=["index"])
    async def costs(pipeline: Pipeline = Depends(get_pipeline)) -> dict[str, Any]:
        rows = await run_in_threadpool(pipeline.store.list_costs, 2000)
        by_op: dict[str, dict[str, float]] = {}
        for record in rows:
            slot = by_op.setdefault(
                record.operation, {"calls": 0, "tokens": 0, "usd": 0.0, "cache_hits": 0}
            )
            slot["calls"] += 1
            slot["tokens"] += record.tokens
            slot["usd"] += record.usd
            slot["cache_hits"] += int(record.cache_hit)
        return {
            "totals": pipeline.meter.snapshot(),
            "by_operation": {
                op: {**slot, "usd": round(slot["usd"], 6)} for op, slot in by_op.items()
            },
            "recent": [r.model_dump(mode="json") for r in rows[-50:]],
        }

    # ------------------------------------------------------------------ maintenance
    @router.post("/maintenance/recompute", tags=["maintenance"])
    async def recompute(
        batch_size: int | None = Query(default=None, ge=1, le=500),
        drain: bool = Query(default=False),
        pipeline: Pipeline = Depends(get_pipeline),
    ) -> dict[str, Any]:
        """Kick the recompute loop by hand. The worker does this on its own; the endpoint
        exists for the demo and for single-process runs."""
        report = await run_in_threadpool(
            pipeline.drain if drain else pipeline.recompute_dirty,
            *([] if drain else [batch_size]),
        )
        return report.as_dict()

    @router.post("/maintenance/compact", tags=["maintenance"])
    async def compact(pipeline: Pipeline = Depends(get_pipeline)) -> dict[str, Any]:
        report = await run_in_threadpool(pipeline.compact)
        return report.as_dict()

    @router.get("/maintenance/integrity", tags=["maintenance"])
    async def integrity(pipeline: Pipeline = Depends(get_pipeline)) -> dict[str, Any]:
        """Provenance invariants, checkable live. All three lists must stay empty."""
        return await run_in_threadpool(_integrity, pipeline)

    # ------------------------------------------------------------------ ops
    @router.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @router.get("/ready", tags=["ops"])
    async def ready(pipeline: Pipeline = Depends(get_pipeline), response: Response = None):
        store_ok = await run_in_threadpool(pipeline.store.ping)
        queue_ok = True
        depth = 0
        if pipeline.queue is not None and hasattr(pipeline.queue, "depth"):
            try:
                depth = await run_in_threadpool(pipeline.queue.depth)
            except Exception:  # noqa: BLE001
                queue_ok = False
        body = {
            "ready": bool(store_ok and queue_ok),
            "store": store_ok,
            "queue": queue_ok,
            "queue_depth": depth,
            "store_backend": settings.store_backend,
            "queue_backend": settings.queue_backend,
            "llm_backend": settings.llm_backend,
        }
        if not body["ready"] and response is not None:
            response.status_code = 503
        return body

    @router.get("/config", tags=["ops"])
    async def config() -> dict[str, Any]:
        cfg = get_settings()
        return {
            "config_hash": cfg.config_hash,
            "store_backend": cfg.store_backend,
            "queue_backend": cfg.queue_backend,
            "llm_backend": cfg.llm_backend,
            "llm_model": cfg.llm_model,
            "embed_backend": cfg.embed_backend,
            "tau_sim": cfg.tau_sim,
            "tau_name": cfg.tau_name,
            "dirty_batch_size": cfg.dirty_batch_size,
            "compaction_interval_s": cfg.compaction_interval_s,
            "compaction_enabled": cfg.compaction_enabled,
            "seed": cfg.seed,
        }

    if settings.metrics_enabled:

        @router.get("/metrics", include_in_schema=False)
        async def prometheus_metrics() -> Response:
            pipeline = get_pipeline()
            await run_in_threadpool(pipeline.stats)  # refresh gauges on scrape
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # The same surface twice: at the root, which is how the API is normally consumed, and
    # under /api for the single-container deployment where the console shares the origin.
    # The console's own /documents and /query routes would otherwise shadow the API's.
    app.include_router(router)
    app.include_router(router, prefix="/api", include_in_schema=False)

    _mount_frontend(app)
    return app


class _SPAFiles(StaticFiles):
    """Static files with a single-page-app fallback.

    ``StaticFiles(html=True)`` serves index.html for a *directory*, but a client-side route
    like /dashboard is neither a file nor a directory, so it 404s. Deep-linking and a
    browser refresh both hit exactly that case, so unknown paths fall back to index.html
    and let the router in the page decide what they mean.
    """

    async def get_response(self, path: str, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built console from this process, if it was built into the image.

    Only the single-container deployment does this. Under Docker Compose and in dev the
    console is its own service, ``frontend/dist`` is absent, and the mount is skipped.
    """
    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if not dist.is_dir():
        return
    # Mounted last, so every API route registered above wins the match.
    app.mount("/", _SPAFiles(directory=str(dist), html=True), name="console")
    log.info("serving the console from %s", dist)


# ---------------------------------------------------------------------- helpers
async def _apply(pipeline: Pipeline, change: DocumentChange) -> ChurnResult:
    try:
        return await run_in_threadpool(pipeline.apply, change)
    except QueueFullError as exc:
        # Backpressure: a bulk ingest must not melt the workers.
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _graph_projection(pipeline: Pipeline, limit: int) -> dict[str, Any]:
    entities = sorted(pipeline.store.all_entities(), key=lambda e: -e.degree)[:limit]
    keep = {e.entity_id for e in entities}
    nodes = [
        {
            "id": e.entity_id,
            "label": e.canonical_name,
            "type": e.type,
            "degree": e.degree,
            "community_id": e.community_id,
            "mentions": len(e.mentions),
        }
        for e in entities
    ]
    edges = [
        {
            "id": r.relation_id,
            "source": r.source_id,
            "target": r.target_id,
            "type": r.relation_type,
            "support": len(r.provenance),
            "provenance": sorted(r.provenance)[:6],
        }
        for r in pipeline.store.all_relations()
        if r.source_id in keep and r.target_id in keep
    ]
    communities = {
        c.community_id: {"title": c.title, "dirty": c.dirty, "member_count": c.member_count}
        for c in pipeline.store.all_communities()
    }
    return {
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
        "truncated": len(keep) < len(pipeline.store.all_entities()),
    }


def _integrity(pipeline: Pipeline) -> dict[str, Any]:
    store = pipeline.store
    orphans = [e.entity_id for e in store.all_entities() if not e.mentions]
    unsupported = [r.relation_id for r in store.all_relations() if not r.provenance]
    live_chunks = {c.chunk_id for c in store.all_chunks()}
    ghosts = [r.relation_id for r in store.all_relations() if not (r.provenance & live_chunks)]
    return {
        "ok": not (orphans or unsupported or ghosts or dangling_relations(store)),
        "orphan_entities": orphans,
        "unsupported_relations": unsupported,
        "relations_citing_dead_chunks": ghosts,
        "dangling_relations": dangling_relations(store),
    }


app = create_app()
