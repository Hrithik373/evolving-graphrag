/**
 * The churn console: add, edit and delete documents and see exactly what each change did
 * to the graph. The receipt panel is the point of the page — a delete shows the relations
 * it expired, the ones it merely weakened, and the entities it collected.
 */

import { useState } from "react";

import UploadZone from "../components/UploadZone";
import { api, type ChurnResult, type DocumentDetail, type DocumentRow } from "../lib/api";
import { usePolling } from "../lib/hooks";

const SAMPLE = `Helios Labs is an independent systems research organization based in Trondheim.
Helios Labs was founded by Marit Solberg in 2019.

Helios Labs develops Aurora Engine, a streaming graph database.
Aurora Engine uses the Fjord Protocol for replication between clusters.

Marit Solberg leads the systems group at Helios Labs.`;

function Receipt({ result }: { result: ChurnResult }) {
  const gc = result.gc_summary;
  return (
    <div className="card" style={{ borderColor: "var(--border-strong)" }}>
      <h3>
        {result.op} · {result.doc_id}{" "}
        <span className="badge muted">{result.sync_wall_ms.toFixed(1)} ms sync</span>
      </h3>
      <p className="hint">
        {result.op === "noop"
          ? "Content hash unchanged — the index was not touched and no tokens were spent."
          : "What the synchronous half of the write did. Extraction and summarisation happen behind this in a worker."}
      </p>

      <div className="grid cols-3" style={{ marginBottom: gc ? 14 : 0 }}>
        <div className="stat">
          <div className="label">chunks added</div>
          <div className="value">{result.chunks_added}</div>
        </div>
        <div className="stat">
          <div className="label">chunks reused</div>
          <div className="value" style={{ color: result.chunks_reused ? "var(--good-text)" : undefined }}>
            {result.chunks_reused}
          </div>
          <div className="sub">not re-extracted</div>
        </div>
        <div className="stat">
          <div className="label">communities dirtied</div>
          <div className="value">{result.dirty_communities.length}</div>
        </div>
      </div>

      {gc && (
        <>
          <h3 style={{ marginTop: 8 }}>Garbage collection receipt</h3>
          <p className="hint">
            Deletion walks provenance: chunk ids are stripped from every relation that cited
            them. A relation whose provenance empties is gone; one with support left survives,
            weakened.
          </p>
          <div className="grid cols-4">
            <div className="stat">
              <div className="label">relations expired</div>
              <div className="value">{gc.relations_expired}</div>
            </div>
            <div className="stat">
              <div className="label">relations weakened</div>
              <div className="value">{gc.relations_weakened}</div>
              <div className="sub">survived on other sources</div>
            </div>
            <div className="stat">
              <div className="label">entities collected</div>
              <div className="value">{gc.entities_removed}</div>
              <div className="sub">orphaned by the delete</div>
            </div>
            <div className="stat">
              <div className="label">chunks removed</div>
              <div className="value">{gc.chunks_removed}</div>
            </div>
          </div>
        </>
      )}

      {result.mutations.length > 0 && (
        <details style={{ marginTop: 14 }}>
          <summary className="small muted" style={{ cursor: "pointer" }}>
            {result.mutations.length} graph mutations
          </summary>
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead>
                <tr>
                  <th>kind</th>
                  <th>subject</th>
                  <th>provenance</th>
                  <th>communities</th>
                </tr>
              </thead>
              <tbody>
                {result.mutations.slice(0, 40).map((mutation, index) => (
                  <tr key={index}>
                    <td>
                      <code>{mutation.kind}</code>
                    </td>
                    <td className="truncate" style={{ maxWidth: 260 }}>
                      {String(
                        mutation.payload.canonical_name ??
                          mutation.payload.relation_type ??
                          mutation.payload.entity_id ??
                          "",
                      )}
                    </td>
                    <td className="mono small muted">{mutation.provenance.length}</td>
                    <td className="mono small muted">{mutation.affected_communities.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

export default function Documents() {
  const documents = usePolling<DocumentRow[]>(() => api.documents(), 4000);
  const [selected, setSelected] = useState<DocumentDetail | null>(null);
  const [docId, setDocId] = useState("");
  const [uri, setUri] = useState("");
  const [content, setContent] = useState(SAMPLE);
  const [result, setResult] = useState<ChurnResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<ChurnResult>) {
    setBusy(true);
    setError(null);
    try {
      setResult(await action());
      await documents.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function load(id: string) {
    try {
      const detail = await api.document(id);
      setSelected(detail);
      setDocId(detail.doc_id);
      setUri(detail.uri);
      setContent(detail.text || detail.chunks.map((c) => c.text).join("\n\n"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const rows = documents.data ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <p>
            Upload files, or write a document by hand. Each write returns immediately with
            a receipt of what it changed — chunks added and reused, communities dirtied, and
            for a delete the full retraction receipt.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="grid cols-2">
        <div className="stack">
          <UploadZone onIngested={() => void documents.refresh()} />

          <div className="card">
            <h3>{selected ? `Edit ${selected.doc_id}` : "New document"}</h3>
            <p className="hint">
              Editing an existing document re-chunks it and diffs against what is stored.
              Paragraphs you leave alone keep their content-addressed chunk ids, so they are
              neither re-embedded nor re-extracted.
            </p>

            <div className="grid cols-2" style={{ marginBottom: 10 }}>
              <div>
                <label htmlFor="doc-id">document id</label>
                <input
                  id="doc-id"
                  value={docId}
                  onChange={(event) => setDocId(event.target.value)}
                  placeholder="helios-overview"
                />
              </div>
              <div>
                <label htmlFor="doc-uri">uri</label>
                <input
                  id="doc-uri"
                  value={uri}
                  onChange={(event) => setUri(event.target.value)}
                  placeholder="helios-overview.md"
                />
              </div>
            </div>

            <label htmlFor="doc-body">content</label>
            <textarea
              id="doc-body"
              rows={14}
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />

            <div className="row" style={{ marginTop: 12 }}>
              <button
                className="primary"
                disabled={busy || !content.trim() || !docId.trim()}
                onClick={() =>
                  run(() =>
                    selected
                      ? api.updateDocument(docId, content)
                      : api.addDocument({ uri: uri || `${docId}.md`, content, doc_id: docId }),
                  )
                }
              >
                {selected ? "save update" : "add document"}
              </button>
              {selected && (
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      const churn = await api.deleteDocument(selected.doc_id);
                      setSelected(null);
                      return churn;
                    })
                  }
                >
                  delete
                </button>
              )}
              {selected && (
                <button
                  className="ghost"
                  onClick={() => {
                    setSelected(null);
                    setDocId("");
                    setUri("");
                    setContent(SAMPLE);
                  }}
                >
                  new
                </button>
              )}
            </div>
          </div>

          <div className="card">
            <h3>Indexed documents</h3>
            <p className="hint">
              A deleted document keeps its record — status is a tombstone, and the graph it
              supported has already been retracted.
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>document</th>
                    <th>status</th>
                    <th className="num">v</th>
                    <th className="num">chunks</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.doc_id}>
                      <td>
                        <b>{row.doc_id}</b>
                        <div className="small muted truncate" style={{ maxWidth: 220 }}>
                          {row.uri}
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${row.status === "active" ? "good" : "muted"}`}>
                          {row.status === "active" ? "● active" : "○ deleted"}
                        </span>
                      </td>
                      <td className="num">{row.version}</td>
                      <td className="num">{row.chunks}</td>
                      <td>
                        <button className="ghost" onClick={() => void load(row.doc_id)}>
                          open
                        </button>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="empty">
                        No documents yet. Add one, or run <code>make demo</code>.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="stack">
          {result ? (
            <Receipt result={result} />
          ) : (
            <div className="card">
              <h3>Change receipt</h3>
              <p className="hint">
                Apply a change and its effect on the graph appears here: chunks added and
                reused, communities marked dirty, and for a delete the full garbage-collection
                receipt.
              </p>
              <div className="empty">No change applied yet.</div>
            </div>
          )}

          {selected && (
            <div className="card">
              <h3>Chunks of {selected.doc_id}</h3>
              <p className="hint">
                Chunk ids are content-addressed and position-independent — that is what lets an
                edit reuse the paragraphs it did not touch.
              </p>
              <div className="stack">
                {selected.chunks.map((chunk) => (
                  <div key={chunk.chunk_id} style={{ borderTop: "1px solid var(--grid)", paddingTop: 8 }}>
                    <div className="mono small muted truncate">{chunk.chunk_id}</div>
                    <div className="small">{chunk.text}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
