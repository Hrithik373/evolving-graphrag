/**
 * The entity-resolution audit trail.
 *
 * Resolution is GraphRAG's known weak point, so every merge/create decision is recorded
 * with the similarity that drove it and the reason it was taken. Under churn a bad merge
 * is worse than a duplicate: merging two entities fuses their provenance sets, and a later
 * deletion can no longer separate what each document supported.
 */

import { useMemo, useState } from "react";

import { api, type Config, type ResolutionRow } from "../lib/api";
import { usePolling } from "../lib/hooks";
import { BarChart, ChartFrame } from "../components/charts";

export default function Resolution() {
  const { data, error } = usePolling<ResolutionRow[]>(() => api.resolutions(300), 5000);
  const { data: config } = usePolling<Config>(api.config, 0);
  const [only, setOnly] = useState<"all" | "merge" | "create">("all");

  const rows = data ?? [];
  const filtered = only === "all" ? rows : rows.filter((row) => row.decision === only);

  const histogram = useMemo(() => {
    // Similarity distribution of the candidates the resolver actually considered.
    const buckets = ["0.0–0.2", "0.2–0.4", "0.4–0.6", "0.6–0.8", "0.8–1.0"];
    const counts = new Array(buckets.length).fill(0);
    for (const row of rows) {
      const index = Math.min(buckets.length - 1, Math.floor(row.similarity * 5));
      counts[Math.max(0, index)] += 1;
    }
    return buckets.map((label, index) => ({ label, value: counts[index] }));
  }, [rows]);

  const merges = rows.filter((row) => row.decision === "merge").length;
  const mergeRate = rows.length ? merges / rows.length : 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Entity resolution</h1>
          <p>
            Every candidate entity produces a decision row. A merge requires{" "}
            <b>both</b> embedding similarity ≥ τ<sub>sim</sub> and normalised-name similarity
            ≥ τ<sub>name</sub> — either alone merges things that should stay apart.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <div className="stat">
          <div className="label">decisions</div>
          <div className="value">{rows.length}</div>
        </div>
        <div className="stat">
          <div className="label">merges</div>
          <div className="value">{merges}</div>
          <div className="sub">{(mergeRate * 100).toFixed(0)}% of candidates</div>
        </div>
        <div className="stat">
          <div className="label">τ sim</div>
          <div className="value">{config?.tau_sim ?? "—"}</div>
          <div className="sub">cosine threshold</div>
        </div>
        <div className="stat">
          <div className="label">τ name</div>
          <div className="value">{config?.tau_name ?? "—"}</div>
          <div className="sub">name-similarity threshold</div>
        </div>
      </div>

      <div className="grid cols-2" style={{ marginBottom: 14 }}>
        <ChartFrame
          title="Candidate similarity distribution"
          hint="Where the candidates fall relative to the merge threshold. A mass sitting just below τ means the threshold is leaving duplicates behind; a mass just above means it is fusing things that should stay separate."
          table={
            <table>
              <thead>
                <tr>
                  <th>similarity</th>
                  <th className="num">candidates</th>
                </tr>
              </thead>
              <tbody>
                {histogram.map((bucket) => (
                  <tr key={bucket.label}>
                    <td>{bucket.label}</td>
                    <td className="num">{bucket.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
        >
          <BarChart data={histogram} valueLabel="candidates" />
        </ChartFrame>

        <div className="card">
          <h3>Why both thresholds</h3>
          <p className="hint">The failure modes each one alone produces:</p>
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-secondary)", lineHeight: 1.9 }}>
            <li>
              <b>Embedding similarity alone</b> merges two research labs that appear in
              near-identical contexts — same shape of thing, same surrounding words.
            </li>
            <li>
              <b>Name similarity alone</b> merges "Apple Inc" the company with "Apple" the
              fruit anywhere a corpus contains both.
            </li>
            <li>
              <b>A bad merge is worse than a duplicate here.</b> Merging fuses two provenance
              sets, and once fused a deletion cannot tell which document supported which half
              — the retraction guarantee is lost for both.
            </li>
          </ul>
          <div className="notice" style={{ marginTop: 12 }}>
            The eval sweeps τ<sub>sim</sub> and reports entity count, merge rate and answer
            recall at each setting, so the threshold is chosen from measurement rather than
            taste.
          </div>
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>Decision log</h3>
          <span className="spacer" />
          {(["all", "merge", "create"] as const).map((option) => (
            <button
              key={option}
              className={only === option ? "" : "ghost"}
              onClick={() => setOnly(option)}
            >
              {option}
            </button>
          ))}
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>candidate</th>
                <th>type</th>
                <th>decision</th>
                <th className="num">similarity</th>
                <th>reason</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 120).map((row, index) => (
                <tr key={index}>
                  <td>
                    <b>{row.candidate_name}</b>
                  </td>
                  <td className="muted">{row.candidate_type}</td>
                  <td>
                    <span className={`badge ${row.decision === "merge" ? "good" : "muted"}`}>
                      {row.decision === "merge" ? "⇄ merge" : "+ create"}
                    </span>
                  </td>
                  <td className="num">{row.similarity.toFixed(3)}</td>
                  <td className="small muted">{row.reason}</td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty">
                    No decisions recorded yet — ingest a document.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
