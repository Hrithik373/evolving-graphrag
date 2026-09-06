/**
 * Drag-and-drop / file-picker ingest.
 *
 * Every accepted file becomes one document, and the per-file receipt is the point: the
 * whole argument of this system is that a write tells you exactly what it changed, so a
 * bulk upload shows the same accounting a single add does.
 *
 * Rejections are shown alongside successes rather than aborting the batch — dropping a
 * folder in and being told which files were unsupported beats a single error with nothing
 * ingested.
 */

import { useCallback, useRef, useState } from "react";

import { api, type UploadResult } from "../lib/api";

const ACCEPT = ".md,.markdown,.txt,.text,.rst";

export default function UploadZone({ onIngested }: { onIngested: () => void }) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setBusy(true);
      setError(null);
      setResult(null);
      try {
        const uploaded = await api.uploadDocuments(files);
        setResult(uploaded);
        onIngested();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [onIngested],
  );

  return (
    <div className="card">
      <h3>Upload documents</h3>
      <p className="hint">
        One file becomes one document. Text formats only ({ACCEPT.replaceAll(",", ", ")}) —
        a PDF would need a conversion stage this system does not have, and ingesting
        whatever bytes arrive would fill the graph with entities nobody can trace back to
        real prose.
      </p>

      <div
        role="button"
        tabIndex={0}
        aria-label="Drop files here or press Enter to choose files"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void upload(Array.from(event.dataTransfer.files));
        }}
        style={{
          border: `2px dashed ${dragging ? "var(--series-1)" : "var(--border-strong)"}`,
          background: dragging ? "color-mix(in srgb, var(--series-1) 8%, transparent)" : "transparent",
          borderRadius: "var(--radius)",
          padding: "26px 16px",
          textAlign: "center",
          cursor: busy ? "wait" : "pointer",
          transition: "border-color 120ms, background 120ms",
          opacity: busy ? 0.6 : 1,
        }}
      >
        <div style={{ fontSize: 22, marginBottom: 6 }} aria-hidden>
          ⬆
        </div>
        <div style={{ fontWeight: 550 }}>
          {busy ? "ingesting…" : dragging ? "drop to ingest" : "Drop files here, or click to choose"}
        </div>
        <div className="small muted" style={{ marginTop: 4 }}>
          multiple files welcome · 2 MB each
        </div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          onChange={(event) => void upload(Array.from(event.target.files ?? []))}
        />
      </div>

      {error && (
        <div className="error" style={{ marginTop: 12, marginBottom: 0 }}>
          {error}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 14 }}>
          <div className="row" style={{ marginBottom: 10 }}>
            <span className={`badge ${result.ingested ? "good" : "muted"}`}>
              ● {result.ingested} ingested
            </span>
            {result.rejected > 0 && (
              <span className="badge warn">▲ {result.rejected} rejected</span>
            )}
          </div>

          {result.results.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>document</th>
                    <th>op</th>
                    <th className="num">chunks +</th>
                    <th className="num">reused</th>
                    <th className="num">dirtied</th>
                    <th className="num">sync ms</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((row) => (
                    <tr key={row.doc_id}>
                      <td>
                        <b>{row.doc_id}</b>
                      </td>
                      <td>
                        <span className={`badge ${row.op === "noop" ? "muted" : "good"}`}>
                          {row.op === "noop" ? "unchanged" : row.op}
                        </span>
                      </td>
                      <td className="num">{row.chunks_added}</td>
                      <td className="num">{row.chunks_reused}</td>
                      <td className="num">{row.dirty_communities.length}</td>
                      <td className="num">{row.sync_wall_ms.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.rejections.length > 0 && (
            <div className="notice" style={{ marginTop: 10 }}>
              {result.rejections.map((rejection) => (
                <div key={rejection.filename}>
                  <b>{rejection.filename}</b> — {rejection.reason}
                </div>
              ))}
            </div>
          )}

          {result.results.some((row) => row.op === "noop") && (
            <div className="notice" style={{ marginTop: 10 }}>
              An <b>unchanged</b> file is a no-op: the content hash matched what is already
              indexed, so the graph was not touched and no tokens were spent. Re-uploading
              the same file costs nothing.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
