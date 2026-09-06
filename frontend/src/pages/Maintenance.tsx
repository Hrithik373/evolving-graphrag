/**
 * Operator page: run the maintenance jobs by hand and check the provenance invariants
 * against the live index.
 *
 * In the deployed system the worker does all of this on its own. These controls exist so
 * the behaviour is demonstrable on demand rather than only observable by waiting.
 */

import { useState } from "react";

import {
  api,
  type CompactionReport,
  type Config,
  type Integrity,
  type RecomputeReport,
  type Staleness,
} from "../lib/api";
import { usePolling } from "../lib/hooks";
import { BarChart, ChartFrame } from "../components/charts";

export default function Maintenance() {
  const stale = usePolling<Staleness>(api.staleness, 3000);
  const integrity = usePolling<Integrity>(api.integrity, 8000);
  const { data: config } = usePolling<Config>(api.config, 0);

  const [recompute, setRecompute] = useState<RecomputeReport | null>(null);
  const [compaction, setCompaction] = useState<CompactionReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(name: string, action: () => Promise<T>, set: (value: T) => void) {
    setBusy(name);
    setError(null);
    try {
      set(await action());
      await stale.refresh();
      await integrity.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const checks = integrity.data
    ? [
        { label: "orphan entities", value: integrity.data.orphan_entities.length },
        { label: "unsupported relations", value: integrity.data.unsupported_relations.length },
        { label: "relations citing dead chunks", value: integrity.data.relations_citing_dead_chunks.length },
        { label: "dangling relations", value: integrity.data.dangling_relations.length },
      ]
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Maintenance</h1>
          <p>
            Selective recompute keeps summaries fresh; periodic compaction keeps community
            structure from drifting. Incremental writes are cheap and local, compaction is
            expensive and global and runs off the hot path — the same bargain an LSM-tree
            makes.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="grid cols-2">
        <div className="card">
          <h3>Selective recompute</h3>
          <p className="hint">
            Pulls the oldest dirty communities and rewrites only their summaries. Clean
            communities are never touched — that is the whole cost argument, and{" "}
            <code>left clean</code> below is the measurement of it.
          </p>
          <div className="row" style={{ marginBottom: 12 }}>
            <button
              disabled={busy !== null || (stale.data?.dirty_count ?? 0) === 0}
              onClick={() => void run("one", () => api.recompute({ batchSize: 1 }), setRecompute)}
            >
              {busy === "one" ? "working…" : "recompute one"}
            </button>
            <button
              className="primary"
              disabled={busy !== null || (stale.data?.dirty_count ?? 0) === 0}
              onClick={() => void run("drain", () => api.recompute({ drain: true }), setRecompute)}
            >
              {busy === "drain" ? "draining…" : "drain the dirty set"}
            </button>
            <span className="spacer" />
            <span className={`badge ${(stale.data?.dirty_count ?? 0) === 0 ? "good" : "warn"}`}>
              {stale.data?.dirty_count ?? 0} dirty
            </span>
          </div>

          {recompute && (
            <div className="grid cols-3">
              <div className="stat">
                <div className="label">recomputed</div>
                <div className="value">{recompute.recomputed}</div>
              </div>
              <div className="stat">
                <div className="label">left clean</div>
                <div className="value" style={{ color: "var(--good-text)" }}>
                  {recompute.skipped_clean}
                </div>
                <div className="sub">summaries not paid for</div>
              </div>
              <div className="stat">
                <div className="label">tokens</div>
                <div className="value">{recompute.tokens}</div>
                <div className="sub">${recompute.usd.toFixed(5)}</div>
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <h3>Compaction</h3>
          <p className="hint">
            Re-runs global community detection and reconciles the result with the existing
            communities by membership overlap. A community whose membership did not change
            keeps its id, its summary and its clean flag — compaction is not a rebuild.
          </p>
          <div className="row" style={{ marginBottom: 12 }}>
            <button
              disabled={busy !== null}
              onClick={() => void run("compact", api.compact, setCompaction)}
            >
              {busy === "compact" ? "clustering…" : "run compaction"}
            </button>
            {config && (
              <span className="badge muted">
                scheduled every {Math.round(config.compaction_interval_s / 60)} min
                {config.compaction_enabled ? "" : " (disabled)"}
              </span>
            )}
          </div>

          {compaction && (
            <div className="grid cols-3">
              <div className="stat">
                <div className="label">communities</div>
                <div className="value">
                  {compaction.communities_before} → {compaction.communities_after}
                </div>
              </div>
              <div className="stat">
                <div className="label">unchanged</div>
                <div className="value" style={{ color: "var(--good-text)" }}>
                  {compaction.stable_communities}
                </div>
                <div className="sub">kept their summaries</div>
              </div>
              <div className="stat">
                <div className="label">entities moved</div>
                <div className="value">{compaction.entities_moved}</div>
                <div className="sub">{compaction.wall_ms.toFixed(0)} ms</div>
              </div>
            </div>
          )}
        </div>

        <ChartFrame
          title="Provenance invariants"
          hint="Checked against the live index. Every bar must be zero — a non-zero bar means the graph is asserting something no surviving document supports."
          table={
            <table>
              <thead>
                <tr>
                  <th>check</th>
                  <th className="num">violations</th>
                </tr>
              </thead>
              <tbody>
                {checks.map((check) => (
                  <tr key={check.label}>
                    <td>{check.label}</td>
                    <td className="num">{check.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
        >
          {integrity.data ? (
            <>
              <div className="row" style={{ marginBottom: 12 }}>
                <span className={`badge ${integrity.data.ok ? "good" : "bad"}`}>
                  {integrity.data.ok ? "● all invariants hold" : "✕ invariant violated"}
                </span>
              </div>
              {integrity.data.ok ? (
                <div className="notice">
                  No orphan entities, no unsupported relations, and nothing citing a chunk
                  that no longer exists. This is the state deletion is supposed to leave the
                  index in, verified rather than assumed.
                </div>
              ) : (
                <BarChart data={checks} valueLabel="violations" colorIndex={1} />
              )}
            </>
          ) : (
            <div className="empty">Checking…</div>
          )}
        </ChartFrame>

        <div className="card">
          <h3>Active configuration</h3>
          <p className="hint">
            Read from the environment through pydantic-settings. The <code>config_hash</code>{" "}
            is stamped onto every eval run, so a figure is always traceable to the settings
            that produced it.
          </p>
          {config && (
            <div className="table-wrap">
              <table>
                <tbody>
                  {Object.entries(config).map(([key, value]) => (
                    <tr key={key}>
                      <td className="muted">{key}</td>
                      <td className="mono">{String(value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
