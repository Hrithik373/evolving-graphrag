"""API contract tests, including the one that matters most: writes never block on a model."""

from __future__ import annotations

import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from egraph.api.app import create_app
from egraph.api.deps import get_pipeline
from egraph.pipeline import build_pipeline
from egraph.workers.queue import build_queue
from fixtures.mini_corpus import BASE_DOCS, UPDATED_DOCS


@pytest.fixture
def client():
    # Wired exactly as api.deps does it, queue included. Building the pipeline without a
    # queue here would exercise a path the running server never takes - which is how a
    # receipt bug reached the UI while these tests stayed green.
    settings = make_settings()
    holder: dict = {}
    queue = build_queue(settings, lambda: holder["pipeline"])
    pipe = build_pipeline(settings, queue=queue)
    holder["pipeline"] = pipe
    pipe.reset()
    app = create_app()
    app.dependency_overrides[get_pipeline] = lambda: pipe
    with TestClient(app) as test_client:
        test_client.pipeline = pipe
        yield test_client
    pipe.close()


def seed(client) -> None:
    for doc_id, text in BASE_DOCS.items():
        response = client.post(
            "/documents", json={"uri": f"{doc_id}.md", "content": text, "doc_id": doc_id}
        )
        assert response.status_code == 202, response.text
    client.post("/maintenance/recompute", params={"drain": True})


def test_health_and_readiness(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["ready"] is True
    assert ready["store"] is True


def test_add_document_returns_immediately_with_intent(client):
    response = client.post(
        "/documents",
        json={"uri": "helios.md", "content": BASE_DOCS["helios-overview"], "doc_id": "helios"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["doc_id"] == "helios"
    assert body["chunks_added"] > 0
    assert body["sync_wall_ms"] >= 0


def test_reposting_the_same_content_is_a_noop(client):
    payload = {"uri": "helios.md", "content": BASE_DOCS["helios-overview"], "doc_id": "helios"}
    client.post("/documents", json=payload)
    second = client.post("/documents", json=payload).json()
    assert second["op"] == "noop"
    assert second["status"] == "unchanged"


def test_update_reports_chunk_reuse(client):
    seed(client)
    body = client.put("/documents/northwind", json={"content": UPDATED_DOCS["northwind"]}).json()
    assert body["op"] == "update"
    assert body["chunks_reused"] >= 1
    assert body["dirty_communities"] or body["mutations"]


def test_delete_returns_its_gc_receipt(client):
    seed(client)
    body = client.request("DELETE", "/documents/kestrel-systems").json()
    assert body["op"] == "delete"
    gc = body["gc_summary"]
    assert gc["relations_expired"] > 0
    assert gc["entities_removed"] > 0
    assert gc["chunks_removed"] > 0


def test_delete_of_an_unknown_document_is_404(client):
    assert client.request("DELETE", "/documents/nope").status_code == 404


def test_query_returns_provenance_and_freshness(client):
    seed(client)
    body = client.post("/query", json={"question": "Who founded Helios Labs?"}).json()
    assert body["text"]
    assert body["citations"]
    assert "stale_communities_touched" in body
    assert body["mode"] == "dual"


def test_query_modes_all_work(client):
    seed(client)
    for mode in ("local", "global", "dual"):
        body = client.post(
            "/query",
            json={"question": "Which protocol did the Fjord Protocol replace?", "mode": mode},
        ).json()
        assert body["mode"] == mode
        assert body["text"]


def test_staleness_and_stats_reflect_reality(client):
    seed(client)
    stats = client.get("/index/stats").json()
    assert stats["entities"] > 0 and stats["relations"] > 0
    assert stats["active_documents"] == len(BASE_DOCS)

    client.put("/documents/northwind", json={"content": UPDATED_DOCS["northwind"]})
    staleness = client.get("/index/staleness").json()
    assert staleness["dirty_count"] > 0
    assert 0 < staleness["dirty_fraction"] <= 1

    client.post("/maintenance/recompute", params={"drain": True})
    assert client.get("/index/staleness").json()["dirty_count"] == 0


def test_integrity_endpoint_reports_a_clean_index(client):
    seed(client)
    client.request("DELETE", "/documents/northwind")
    body = client.get("/maintenance/integrity").json()
    assert body["ok"] is True, body


def test_graph_projection_is_consistent(client):
    seed(client)
    graph = client.get("/index/graph").json()
    node_ids = {node["id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
        assert edge["support"] >= 1


def test_communities_endpoint_exposes_dirt(client):
    seed(client)
    client.put("/documents/northwind", json={"content": UPDATED_DOCS["northwind"]})
    communities = client.get("/index/communities").json()
    assert communities
    assert any(c["dirty"] for c in communities)
    assert all("summary" in c for c in communities)


def test_costs_endpoint_accounts_for_every_call(client):
    seed(client)
    costs = client.get("/index/costs").json()
    assert costs["totals"]["calls"] > 0
    assert set(costs["by_operation"]) & {"extract", "embed", "summarize"}


def test_resolutions_are_queryable(client):
    seed(client)
    rows = client.get("/index/resolutions").json()
    assert rows
    assert {r["decision"] for r in rows} <= {"merge", "create"}


def test_metrics_endpoint_exports_the_freshness_gauge(client):
    seed(client)
    text = client.get("/metrics").text
    assert "egraph_dirty_communities" in text
    assert "egraph_llm_tokens_total" in text
    assert "egraph_summary_recompute_total" in text


def test_config_endpoint_stamps_a_config_hash(client):
    body = client.get("/config").json()
    assert len(body["config_hash"]) == 16
    assert body["llm_backend"] == "mock"


# ------------------------------------------------------------------ upload
def _file(name: str, text: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, text.encode("utf-8"), "text/plain"))


def test_upload_ingests_text_files(client):
    response = client.post(
        "/documents/upload",
        files=[
            _file("helios.md", BASE_DOCS["helios-overview"]),
            _file("aurora.txt", BASE_DOCS["aurora-engine"]),
        ],
    )
    assert response.status_code == 202
    body = response.json()
    assert body["ingested"] == 2
    assert body["rejected"] == 0
    assert {r["doc_id"] for r in body["results"]} == {"helios", "aurora"}
    assert all(r["chunks_added"] > 0 for r in body["results"])
    assert {d["doc_id"] for d in client.get("/documents").json()} == {"helios", "aurora"}


def test_upload_rejects_unsupported_types_without_failing_the_batch(client):
    """A rejected file must not cost the caller the files that were fine."""
    body = client.post(
        "/documents/upload",
        files=[
            _file("good.md", BASE_DOCS["helios-overview"]),
            ("files", ("scan.pdf", b"%PDF-1.7 binary", "application/pdf")),
        ],
    ).json()
    assert body["ingested"] == 1
    assert body["rejected"] == 1
    assert body["rejections"][0]["filename"] == "scan.pdf"
    assert "unsupported type" in body["rejections"][0]["reason"]
    assert [d["doc_id"] for d in client.get("/documents").json()] == ["good"]


def test_upload_rejects_non_utf8_and_empty_files(client):
    body = client.post(
        "/documents/upload",
        files=[
            ("files", ("binary.txt", b"\xff\xfe\x00\x01\x80", "text/plain")),
            _file("blank.md", "   \n\n  "),
        ],
    ).json()
    assert body["ingested"] == 0
    reasons = {r["filename"]: r["reason"] for r in body["rejections"]}
    assert "UTF-8" in reasons["binary.txt"]
    assert "empty" in reasons["blank.md"]


def test_uploading_the_same_file_twice_is_a_noop(client):
    payload = [_file("helios.md", BASE_DOCS["helios-overview"])]
    first = client.post("/documents/upload", files=payload).json()
    second = client.post("/documents/upload", files=payload).json()
    assert first["results"][0]["op"] == "add"
    assert second["results"][0]["op"] == "noop"


def test_an_add_receipt_reports_the_work_that_actually_happened(client):
    """With no worker configured extraction runs inline, so the receipt must show it.

    A receipt claiming zero mutations while the graph gained entities is a lie about what
    the write did - and it is what the Documents page shows the user.
    """
    body = client.post(
        "/documents",
        json={"uri": "helios.md", "content": BASE_DOCS["helios-overview"], "doc_id": "helios"},
    ).json()
    stats = client.get("/index/stats").json()

    assert stats["entities"] > 0, "the write must have built a graph"
    assert body["mutations"], "inline extraction happened but the receipt reported nothing"
    assert body["dirty_communities"], "new entities must dirty the communities they land in"
    assert {m["kind"] for m in body["mutations"]} & {"add_entity", "add_relation"}


def test_a_receipt_reports_only_what_that_change_dirtied(client):
    """Not everything currently outstanding - only this write's contribution."""
    client.post(
        "/documents",
        json={"uri": "helios.md", "content": BASE_DOCS["helios-overview"], "doc_id": "helios"},
    )
    # Leave the index dirty on purpose, then make an unrelated write.
    stale_before = {
        c["community_id"] for c in client.get("/index/communities").json() if c["dirty"]
    }
    assert stale_before, "this test needs a dirty index to be meaningful"

    body = client.post(
        "/documents",
        json={"uri": "kestrel.md", "content": BASE_DOCS["kestrel-systems"], "doc_id": "kestrel"},
    ).json()
    assert not (
        set(body["dirty_communities"]) & stale_before
    ), "the receipt attributed pre-existing staleness to this write"
