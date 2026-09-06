"""Command line: the same pipeline the service runs, driven from a terminal.

egraph ingest fixtures/mini_corpus     # or any directory of .md/.txt
egraph demo                            # scripted add -> query -> update -> delete
egraph query "Who founded Helios Labs?"
egraph churn update northwind ./new.md
egraph staleness | egraph stats | egraph integrity
egraph recompute --drain | egraph compact
egraph eval --output results/
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from egraph import __version__
from egraph.ingest.loader import load_corpus, make_doc_id
from egraph.pipeline import Pipeline, build_pipeline
from egraph.schemas import DocumentChange, QueryRequest
from egraph.settings import get_settings

app = typer.Typer(help="evolving-graphrag: incrementally-maintained knowledge-graph RAG.")
console = Console()


def _pipeline() -> Pipeline:
    logging.basicConfig(level="WARNING", format="%(levelname)s %(name)s %(message)s")
    return build_pipeline(get_settings())


@app.command()
def version() -> None:
    """Print the version and the active backends."""
    cfg = get_settings()
    console.print(f"evolving-graphrag {__version__}")
    console.print(
        f"store={cfg.store_backend} queue={cfg.queue_backend} "
        f"llm={cfg.llm_backend} embed={cfg.embed_backend} config_hash={cfg.config_hash}"
    )


@app.command()
def ingest(
    directory: str = typer.Argument(..., help="Directory of .md/.txt documents"),
    drain: bool = typer.Option(True, help="Recompute dirty summaries before returning"),
) -> None:
    """Ingest a directory of documents."""
    pipeline = _pipeline()
    changes = load_corpus(directory)
    if not changes:
        console.print(f"[yellow]no .md/.txt files under {directory}")
        raise typer.Exit(1)

    table = Table("document", "op", "chunks +", "reused", "sync ms")
    for change in changes:
        result = pipeline.apply(change)
        table.add_row(
            result.doc_id,
            result.op,
            str(result.chunks_added),
            str(result.chunks_reused),
            f"{result.sync_wall_ms:.1f}",
        )
    console.print(table)

    if drain:
        report = pipeline.drain()
        console.print(f"recomputed {report.recomputed} community summaries")
    _print_stats(pipeline)


@app.command()
def demo(
    seed_only: bool = typer.Option(False, help="Only load the corpus, skip the churn")
) -> None:
    """Scripted demo over the bundled mini-corpus: ingest, query, update, delete."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from fixtures.mini_corpus import BASE_DOCS, DELETION_SET, UPDATED_DOCS

    pipeline = _pipeline()
    console.rule("[bold]ingest")
    for doc_id, text in BASE_DOCS.items():
        result = pipeline.apply(
            DocumentChange(op="add", doc_id=doc_id, uri=f"{doc_id}.md", content=text)
        )
        console.print(
            f"  add {doc_id}: +{result.chunks_added} chunks in {result.sync_wall_ms:.1f} ms"
        )
    report = pipeline.drain()
    console.print(f"  {report.recomputed} summaries written")
    _print_stats(pipeline)
    if seed_only:
        return

    console.rule("[bold]query")
    answer = pipeline.query(QueryRequest(question="Who founded Helios Labs?"))
    console.print(f"  {answer.text[:220]}")
    console.print(f"  cited: {answer.citations}  stale touched: {answer.stale_communities_touched}")

    console.rule("[bold]update (one paragraph changes)")
    result = pipeline.apply(
        DocumentChange(op="update", doc_id="northwind", content=UPDATED_DOCS["northwind"])
    )
    total = len(pipeline.store.all_communities())
    console.print(
        f"  chunks: +{result.chunks_added} new, {result.chunks_reused} reused, "
        f"-{result.chunks_removed} removed ({result.sync_wall_ms:.1f} ms)"
    )
    console.print(f"  dirty: {len(pipeline.store.dirty_communities())} of {total} communities")
    report = pipeline.drain()
    console.print(f"  recomputed {report.recomputed} of {total} - the rest were left alone")

    console.rule("[bold]delete")
    for doc_id in DELETION_SET:
        result = pipeline.apply(DocumentChange(op="delete", doc_id=doc_id))
        gc = result.gc_summary
        console.print(
            f"  {doc_id}: {gc.relations_expired} relations expired, {gc.relations_weakened} "
            f"weakened, {gc.entities_removed} entities collected ({result.sync_wall_ms:.1f} ms)"
        )
    pipeline.drain()

    console.rule("[bold]after deletion")
    for question in ("Who founded Northwind Analytics?", "Who founded Helios Labs?"):
        answer = pipeline.query(QueryRequest(question=question))
        console.print(f"  Q {question}\n    {answer.text[:200]}")
    _print_stats(pipeline)
    console.print(f"\n  cost so far: {pipeline.meter.snapshot()}")


@app.command()
def query(
    question: str,
    mode: str = typer.Option("dual", help="local | global | dual"),
    show_context: bool = typer.Option(False, help="Print the retrieved context too"),
) -> None:
    """Ask the index a question."""
    pipeline = _pipeline()
    request = QueryRequest(question=question, mode=mode)  # type: ignore[arg-type]
    if show_context:
        console.print(pipeline.retriever.retrieve(request).context)
        console.rule()
    answer = pipeline.query(request)
    console.print(answer.text)
    console.print(f"\n[dim]cited: {', '.join(answer.citations) or 'nothing'}")
    console.print(
        f"[dim]communities: {len(answer.used_communities)} "
        f"({answer.stale_communities_touched} stale) - {answer.latency_ms:.1f} ms"
    )


churn_app = typer.Typer(help="Apply a single document change.")
app.add_typer(churn_app, name="churn")


@churn_app.command("add")
def churn_add(path: str, doc_id: str = typer.Option(None)) -> None:
    _apply_file("add", path, doc_id)


@churn_app.command("update")
def churn_update(doc_id: str, path: str) -> None:
    _apply_file("update", path, doc_id)


@churn_app.command("delete")
def churn_delete(doc_id: str) -> None:
    pipeline = _pipeline()
    result = pipeline.apply(DocumentChange(op="delete", doc_id=doc_id))
    if result.op == "noop":
        console.print(f"[yellow]{doc_id}: {result.status}")
        raise typer.Exit(1)
    console.print_json(json.dumps(result.gc_summary.model_dump(mode="json"), indent=2))
    console.print(
        f"[dim]{result.sync_wall_ms:.1f} ms, {len(result.dirty_communities)} communities dirtied"
    )


def _apply_file(op: str, path: str, doc_id: str | None) -> None:
    file = Path(path)
    if not file.exists():
        console.print(f"[red]no such file: {path}")
        raise typer.Exit(1)
    pipeline = _pipeline()
    result = pipeline.apply(
        DocumentChange(
            op=op,  # type: ignore[arg-type]
            doc_id=doc_id or make_doc_id(file.name),
            uri=str(file.as_posix()),
            content=file.read_text(encoding="utf-8"),
        )
    )
    console.print(
        f"{result.op} {result.doc_id}: +{result.chunks_added} chunks, "
        f"{result.chunks_reused} reused, -{result.chunks_removed} removed "
        f"({result.sync_wall_ms:.1f} ms)"
    )


@app.command()
def staleness() -> None:
    """Current index freshness."""
    console.print_json(_pipeline().staleness().model_dump_json(indent=2))


@app.command()
def stats() -> None:
    """Index size and cost to date."""
    _print_stats(_pipeline())


@app.command()
def integrity() -> None:
    """Check the provenance invariants against the live index."""
    pipeline = _pipeline()
    orphans = [e.entity_id for e in pipeline.store.all_entities() if not e.mentions]
    unsupported = [r.relation_id for r in pipeline.store.all_relations() if not r.provenance]
    live = {c.chunk_id for c in pipeline.store.all_chunks()}
    ghosts = [r.relation_id for r in pipeline.store.all_relations() if not (r.provenance & live)]
    ok = not (orphans or unsupported or ghosts)
    console.print(f"orphan entities:            {len(orphans)}")
    console.print(f"unsupported relations:      {len(unsupported)}")
    console.print(f"relations citing dead chunks: {len(ghosts)}")
    console.print("[green]invariants hold" if ok else "[red]INVARIANT VIOLATION")
    raise typer.Exit(0 if ok else 1)


@app.command()
def recompute(
    batch: int = typer.Option(None, help="How many communities to recompute"),
    drain: bool = typer.Option(False, help="Recompute until nothing is dirty"),
) -> None:
    """Re-summarise dirty communities."""
    pipeline = _pipeline()
    report = pipeline.drain() if drain else pipeline.recompute_dirty(batch)
    console.print_json(json.dumps(report.as_dict(), indent=2))


@app.command()
def compact() -> None:
    """Run a full re-clustering pass."""
    console.print_json(json.dumps(_pipeline().compact().as_dict(), indent=2))


@app.command()
def eval(
    output: str = typer.Option("results", help="Where to write figures and tables"),
    quick: bool = typer.Option(False, help="Fewer sweep points"),
    systems: str = typer.Option("", help="Comma-separated subset of systems"),
) -> None:
    """Run the full benchmark and regenerate every figure."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from egraph.eval.runner import run_full_eval, write_results

    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    cfg = get_settings()
    console.print(f"[bold]running eval[/] config_hash={cfg.config_hash} seed={cfg.seed}")
    run = run_full_eval(cfg, systems.split(",") if systems else None, quick=quick)
    written = write_results(run, output)

    table = Table("system", "scenario", "F1", "recall", "stale rate", "update tokens", "recomputed")
    for outcome in run.scenarios:
        table.add_row(
            outcome.system,
            outcome.scenario,
            f"{outcome.scores.f1:.3f}",
            f"{outcome.scores.recall:.3f}",
            f"{outcome.scores.stale_answer_rate:.3f}",
            str(outcome.update_cost.get("tokens", 0)),
            str(outcome.update_cost.get("communities_recomputed", 0)),
        )
    console.print(table)
    console.print(f"\nwrote: {', '.join(str(p) for p in written.values())}")
    console.print(f"[dim]{run.wall_s:.1f}s")


def _print_stats(pipeline: Pipeline) -> None:
    stats = pipeline.stats()
    staleness = pipeline.staleness()
    table = Table("metric", "value")
    for key, value in stats.model_dump().items():
        table.add_row(key, str(value))
    table.add_row("dirty_fraction", f"{staleness.dirty_fraction:.3f}")
    console.print(table)


if __name__ == "__main__":
    app()
