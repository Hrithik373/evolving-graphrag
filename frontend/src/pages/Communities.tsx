/**
 * Community summaries and their dirty state — the thing selective recompute is selective
 * about. Sorted dirty-first, because that is the working set.
 */

import { useState } from "react";

import { api, type CommunityRow } from "../lib/api";
import { usePolling } from "../lib/hooks";

export default function Communities() {
  const { data, error, refresh } = usePolling<CommunityRow[]>(api.communities, 3000);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const rows = data ?? [];
  const dirty = rows.filter((row) => row.dirty);

  async function recompute(drain: boolean) {
    setBusy(true);
    try {
      const report = await api.recompute(drain ? { drain: true } : { batchSize: 1 });
      setNote(
        `recomputed ${report.recomputed} · left clean ${report.skipped_clean} · ` +
          `${report.remaining_dirty} still dirty · ${report.tokens} tokens`,
      );
      await refresh();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Communities</h1>
          <p>
            Each community has one LLM-written summary covering its members. When a member
            changes, only that community is marked dirty — and only dirty communities are
            ever re-summarised.
          </p>
        </div>
        <div className="row">
          <button disabled={busy || dirty.length === 0} onClick={() => void recompute(false)}>
            recompute one
          </button>
          <button
            className="primary"
            disabled={busy || dirty.length === 0}
            onClick={() => void recompute(true)}
          >
            recompute all dirty
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {note && <div className="notice" style={{ marginBottom: 14 }}>{note}</div>}

      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <div className="stat">
          <div className="label">communities</div>
          <div className="value">{rows.length}</div>
        </div>
        <div className="stat">
          <div className="label">dirty</div>
          <div className="value" style={{ color: dirty.length ? "var(--critical)" : "var(--good-text)" }}>
            {dirty.length}
          </div>
          <div className="sub">awaiting re-summarisation</div>
        </div>
        <div className="stat">
          <div className="label">clean</div>
          <div className="value">{rows.length - dirty.length}</div>
          <div className="sub">untouched by recent changes</div>
        </div>
        <div className="stat">
          <div className="label">largest</div>
          <div className="value">
            {rows.reduce((max, row) => Math.max(max, row.member_count), 0)}
          </div>
          <div className="sub">members in one community</div>
        </div>
      </div>

      <div className="stack">
        {rows.map((row) => (
          <div
            className="card"
            key={row.community_id}
            style={row.dirty ? { borderColor: "color-mix(in srgb, var(--warning) 55%, transparent)" } : undefined}
          >
            <div className="row" style={{ marginBottom: 6 }}>
              <h3 style={{ margin: 0 }}>{row.title || row.community_id}</h3>
              <span className={`badge ${row.dirty ? "warn" : "good"}`}>
                {row.dirty ? "▲ stale" : "● current"}
              </span>
              <span className="badge muted">{row.member_count} members</span>
              <span className="spacer" />
              <code className="small muted">{row.community_id}</code>
            </div>

            {row.summary ? (
              <p style={{ margin: "4px 0 10px", color: "var(--text-secondary)" }}>{row.summary}</p>
            ) : (
              <p className="hint">No summary written yet — this community has never been clean.</p>
            )}

            {row.dirty && row.drifted_members.length > 0 && (
              <div className="notice">
                <b>{row.drifted_members.length}</b> member
                {row.drifted_members.length === 1 ? "" : "s"} changed since this summary was
                written. That difference is exactly why the community is dirty — the summary
                records the member set it covered, so drift is detected rather than guessed at.
              </div>
            )}
          </div>
        ))}
        {rows.length === 0 && (
          <div className="empty">
            No communities yet. Ingest some documents and the graph will partition itself.
          </div>
        )}
      </div>
    </>
  );
}
