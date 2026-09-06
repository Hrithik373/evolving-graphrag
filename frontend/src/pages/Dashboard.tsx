/**
 * The live view of index freshness.
 *
 * This is the page the demo runs on: add or delete a document in another tab and watch
 * `dirty communities` spike and then drain while everything else stays untouched.
 */

import { api, type CostBreakdown, type IndexStats, type Staleness } from "../lib/api";
import { usePolling, useHistory } from "../lib/hooks";
import { ChartFrame, LineChart, StackedBar, formatNumber } from "../components/charts";

function StatTile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "good" | "warn" | "bad";
}) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div
        className="value"
        style={
          tone === "bad"
            ? { color: "var(--critical)" }
            : tone === "good"
              ? { color: "var(--good-text)" }
              : undefined
        }
      >
        {value}
      </div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export default function Dashboard() {
  const stale = usePolling<Staleness>(api.staleness, 2000);
  const stats = usePolling<IndexStats>(api.stats, 2000);
  const costs = usePolling<CostBreakdown>(api.costs, 5000);

  const history = useHistory(stale.data, 90);
  const sizeHistory = useHistory(stats.data, 90);

  const dirty = stale.data?.dirty_count ?? 0;
  const total = stale.data?.community_count ?? 0;
  const fraction = stale.data?.dirty_fraction ?? 0;

  const stalenessPoints = history.map((sample, index) => ({
    x: index,
    values: [sample.value.dirty_count, total],
    label: new Date(sample.at).toLocaleTimeString(),
  }));

  const sizePoints = sizeHistory.map((sample, index) => ({
    x: index,
    values: [sample.value.entities, sample.value.relations, sample.value.communities],
    label: new Date(sample.at).toLocaleTimeString(),
  }));

  const byOp = costs.data?.by_operation ?? {};
  const opOrder = ["extract", "summarize", "embed", "answer"].filter((op) => op in byOp);
  const cacheLookups =
    (costs.data?.totals.calls ?? 0) > 0 ? (costs.data?.totals.cache_hits ?? 0) : 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Index freshness</h1>
          <p>
            A document change marks only the communities it touched as dirty. Everything
            else keeps its summary. Watch the dirty count spike on a write and drain as the
            worker catches up — that gap is the entire cost saving.
          </p>
        </div>
        <button className="ghost" onClick={() => { void stale.refresh(); void stats.refresh(); }}>
          refresh
        </button>
      </div>

      {stale.error && <div className="error">API unreachable: {stale.error}</div>}

      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <StatTile
          label="Dirty communities"
          value={String(dirty)}
          sub={total ? `of ${total} · ${(fraction * 100).toFixed(0)}% stale` : "index empty"}
          tone={dirty === 0 ? "good" : dirty > total / 2 ? "bad" : "warn"}
        />
        <StatTile
          label="Oldest dirty"
          value={
            stale.data && stale.data.oldest_dirty_age_s > 0
              ? `${stale.data.oldest_dirty_age_s.toFixed(0)}s`
              : "—"
          }
          sub="longest a summary has lagged"
        />
        <StatTile
          label="Graph"
          value={`${stats.data?.entities ?? 0}`}
          sub={`entities · ${stats.data?.relations ?? 0} relations`}
        />
        <StatTile
          label="Spend"
          value={`$${(stats.data?.total_usd ?? 0).toFixed(4)}`}
          sub={`${formatNumber(stats.data?.total_tokens ?? 0)} tokens metered`}
        />
      </div>

      <div className="grid cols-2">
        <ChartFrame
          title="Staleness over time"
          hint="Dirty summaries against the total. The spike-and-drain shape is selective recompute; a full reindex would be one flat line at the total."
          table={
            <table>
              <thead>
                <tr>
                  <th>time</th>
                  <th className="num">dirty</th>
                  <th className="num">total</th>
                </tr>
              </thead>
              <tbody>
                {history
                  .slice(-14)
                  .reverse()
                  .map((sample) => (
                    <tr key={sample.at}>
                      <td>{new Date(sample.at).toLocaleTimeString()}</td>
                      <td className="num">{sample.value.dirty_count}</td>
                      <td className="num">{sample.value.community_count}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          }
        >
          <LineChart
            points={stalenessPoints}
            series={["dirty", "total communities"]}
            yLabel="communities"
          />
        </ChartFrame>

        <ChartFrame
          title="Index size"
          hint="Deletions pull these down. An append-only index never would — that is the difference this project is about."
          table={
            <table>
              <thead>
                <tr>
                  <th>metric</th>
                  <th className="num">count</th>
                </tr>
              </thead>
              <tbody>
                {stats.data &&
                  (
                    [
                      ["documents (active)", stats.data.active_documents],
                      ["chunks", stats.data.chunks],
                      ["entities", stats.data.entities],
                      ["relations", stats.data.relations],
                      ["communities", stats.data.communities],
                      ["dirty communities", stats.data.dirty_communities],
                    ] as const
                  ).map(([label, value]) => (
                    <tr key={label}>
                      <td>{label}</td>
                      <td className="num">{value}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          }
        >
          <LineChart
            points={sizePoints}
            series={["entities", "relations", "communities"]}
            yLabel="count"
          />
        </ChartFrame>

        <ChartFrame
          title="Where the tokens went"
          hint="Every model call is priced by CostMeter. Extraction dominates a build; summarisation dominates maintenance."
          table={
            <table>
              <thead>
                <tr>
                  <th>operation</th>
                  <th className="num">calls</th>
                  <th className="num">tokens</th>
                  <th className="num">usd</th>
                  <th className="num">cache hits</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(byOp).map(([op, row]) => (
                  <tr key={op}>
                    <td>{op}</td>
                    <td className="num">{row.calls}</td>
                    <td className="num">{formatNumber(row.tokens)}</td>
                    <td className="num">${row.usd.toFixed(5)}</td>
                    <td className="num">{row.cache_hits}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
        >
          <StackedBar
            segments={opOrder.map((op) => ({ label: op, value: byOp[op]?.tokens ?? 0 }))}
          />
          <div className="notice" style={{ marginTop: 12 }}>
            {cacheLookups > 0 ? (
              <>
                <b>{cacheLookups}</b> extraction cache hits — chunks that survived an edit and
                were not re-extracted. Under churn this is where the update saving comes from.
              </>
            ) : (
              <>No cache hits yet. Edit a document and re-save it: unchanged paragraphs keep
              their content-addressed ids and skip extraction entirely.</>
            )}
          </div>
        </ChartFrame>

        <div className="card">
          <h3>What this index guarantees</h3>
          <p className="hint">
            These are invariants, not aspirations — the property tests assert them and{" "}
            <code>/maintenance/integrity</code> checks them against the live graph.
          </p>
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-secondary)", lineHeight: 1.9 }}>
            <li>Every entity and relation traces to a chunk that still exists.</li>
            <li>
              Deleting a document removes <b>exactly</b> what that document uniquely
              supported — no more, no less.
            </li>
            <li>Only communities with a changed member are re-summarised.</li>
            <li>Re-ingesting unchanged content is a no-op and costs nothing.</li>
            <li>Every answer reports how many stale summaries it touched.</li>
          </ul>
        </div>
      </div>
    </>
  );
}
