/**
 * Force-directed view of the entity graph, coloured by community.
 *
 * Community colour here is *identity of a region*, not a value scale, and there are more
 * communities than the categorical palette has slots. Rather than cycle hues - which would
 * make two unrelated communities look like the same one - each node carries its community
 * id on hover and in the side panel, and the fill is a deliberately low-saturation
 * neutral-to-blue ramp keyed to community size. Dirty communities are marked with a ring
 * and a label, never by colour alone.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { api, type GraphEdge, type GraphNode, type GraphProjection } from "../lib/api";
import { usePolling } from "../lib/hooks";

interface Simulated extends GraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

const WIDTH = 900;
const HEIGHT = 560;

function layout(nodes: GraphNode[], edges: GraphEdge[], iterations = 260): Simulated[] {
  // Deterministic seeding: the same graph always draws the same way, which matters when a
  // screenshot ends up in the report.
  const simulated: Simulated[] = nodes.map((node, index) => {
    const angle = (index / Math.max(nodes.length, 1)) * Math.PI * 2;
    const radius = 150 + ((index * 37) % 110);
    return {
      ...node,
      x: WIDTH / 2 + Math.cos(angle) * radius,
      y: HEIGHT / 2 + Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
    };
  });
  const index = new Map(simulated.map((node) => [node.id, node]));
  const links = edges
    .map((edge) => ({ source: index.get(edge.source), target: index.get(edge.target), edge }))
    .filter((link) => link.source && link.target) as {
    source: Simulated;
    target: Simulated;
    edge: GraphEdge;
  }[];

  for (let step = 0; step < iterations; step += 1) {
    const cooling = 1 - step / iterations;

    // Repulsion
    for (let i = 0; i < simulated.length; i += 1) {
      for (let j = i + 1; j < simulated.length; j += 1) {
        const a = simulated[i];
        const b = simulated[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distanceSq = dx * dx + dy * dy;
        if (distanceSq < 1) {
          dx = (i - j) * 0.5 || 0.5;
          dy = 0.5;
          distanceSq = 1;
        }
        const force = 2600 / distanceSq;
        const distance = Math.sqrt(distanceSq);
        a.vx += (dx / distance) * force;
        a.vy += (dy / distance) * force;
        b.vx -= (dx / distance) * force;
        b.vy -= (dy / distance) * force;
      }
    }

    // Spring attraction along edges. The rest length shortens as provenance support
    // rises, so a relation asserted by several chunks visibly binds its endpoints tighter
    // than one asserted by a single passage.
    for (const link of links) {
      const dx = link.target.x - link.source.x;
      const dy = link.target.y - link.source.y;
      const distance = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = 120 - Math.min(link.edge.support, 4) * 14;
      const pull = ((distance - rest) / distance) * 0.05;
      link.source.vx += dx * pull;
      link.source.vy += dy * pull;
      link.target.vx -= dx * pull;
      link.target.vy -= dy * pull;
    }

    for (const node of simulated) {
      node.vx += (WIDTH / 2 - node.x) * 0.006;
      node.vy += (HEIGHT / 2 - node.y) * 0.006;
      node.x += Math.max(-24, Math.min(24, node.vx * cooling * 0.4));
      node.y += Math.max(-24, Math.min(24, node.vy * cooling * 0.4));
      node.vx *= 0.72;
      node.vy *= 0.72;
      node.x = Math.max(32, Math.min(WIDTH - 32, node.x));
      node.y = Math.max(28, Math.min(HEIGHT - 28, node.y));
    }
  }
  return simulated;
}

export default function GraphExplorer() {
  const { data, error, refresh } = usePolling<GraphProjection>(() => api.graph(220), 0);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const svgRef = useRef<SVGSVGElement>(null);

  const nodes = useMemo(
    () => (data ? layout(data.nodes, data.edges) : []),
    [data],
  );

  useEffect(() => setSelected(null), [data]);

  if (error) return <div className="error">Could not load the graph: {error}</div>;
  if (!data) return <div className="empty">Loading graph…</div>;

  const communities = Object.keys(data.communities).sort();
  const communityRank = new Map(communities.map((id, index) => [id, index]));
  const positions = new Map(nodes.map((node) => [node.id, node]));

  const matches = (node: GraphNode) =>
    !filter || node.label.toLowerCase().includes(filter.toLowerCase());

  const nodeFill = (node: GraphNode) => {
    if (!node.community_id) return "var(--text-muted)";
    // A gentle ramp across communities: distinguishable, but never pretending to be a
    // meaningful categorical scale when there are more regions than validated slots.
    const rank = communityRank.get(node.community_id) ?? 0;
    const shade = 0.35 + ((rank * 7) % 10) / 18;
    return `color-mix(in srgb, var(--series-1) ${Math.round(shade * 100)}%, var(--surface-2))`;
  };

  const selectedEdges = selected
    ? data.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id)
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Graph</h1>
          <p>
            Entities and the relations between them. Every edge carries the set of chunks
            that support it — click a node to see what a deletion would have to retract.
          </p>
        </div>
        <div className="row">
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="filter entities"
            style={{ width: 200 }}
          />
          <button className="ghost" onClick={() => void refresh()}>
            reload
          </button>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2.2fr) minmax(280px, 1fr)" }}>
        <div className="card" style={{ padding: 8 }}>
          <svg
            ref={svgRef}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            style={{ width: "100%", height: "auto", display: "block" }}
            role="img"
            aria-label="entity relation graph"
          >
            {data.edges.map((edge) => {
              const source = positions.get(edge.source);
              const target = positions.get(edge.target);
              if (!source || !target) return null;
              const active =
                selected && (edge.source === selected.id || edge.target === selected.id);
              return (
                <line
                  key={edge.id}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={active ? "var(--series-2)" : "var(--grid)"}
                  strokeWidth={active ? 2 : Math.min(1 + edge.support * 0.4, 2.4)}
                  opacity={selected && !active ? 0.25 : 0.9}
                />
              );
            })}

            {nodes.map((node) => {
              const dirty = node.community_id
                ? data.communities[node.community_id]?.dirty
                : false;
              const dim = !matches(node);
              const radius = Math.max(6, Math.min(15, 5 + node.degree * 0.9));
              return (
                <g
                  key={node.id}
                  opacity={dim ? 0.18 : 1}
                  style={{ cursor: "pointer" }}
                  onClick={() => setSelected(node)}
                  onMouseEnter={() => setHover(node.id)}
                  onMouseLeave={() => setHover(null)}
                >
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={radius}
                    fill={nodeFill(node)}
                    stroke={
                      selected?.id === node.id
                        ? "var(--series-2)"
                        : dirty
                          ? "var(--warning)"
                          : "var(--surface-1)"
                    }
                    strokeWidth={selected?.id === node.id ? 3 : dirty ? 2.5 : 2}
                    strokeDasharray={dirty && selected?.id !== node.id ? "3 2" : undefined}
                  />
                  {(hover === node.id || selected?.id === node.id || node.degree >= 4 || filter) && (
                    <text
                      x={node.x}
                      y={node.y - radius - 5}
                      textAnchor="middle"
                      fontSize={11}
                      fontWeight={selected?.id === node.id ? 600 : 500}
                      fill="var(--text-primary)"
                      style={{ paintOrder: "stroke", stroke: "var(--surface-1)", strokeWidth: 3 }}
                    >
                      {node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>

          <div className="legend" style={{ padding: "0 8px 6px" }}>
            <span className="key">
              <span
                className="swatch"
                style={{ background: "var(--series-1)", opacity: 0.7 }}
                aria-hidden
              />
              node fill = community
            </span>
            <span className="key">
              <span
                className="swatch"
                style={{
                  background: "transparent",
                  border: "2px dashed var(--warning)",
                  borderRadius: 999,
                }}
                aria-hidden
              />
              dashed ring = community summary is stale
            </span>
            <span className="key">node size = degree · edge width = provenance support</span>
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <h3>{selected ? selected.label : "Select an entity"}</h3>
            {selected ? (
              <>
                <div className="row" style={{ marginBottom: 12 }}>
                  <span className="badge">{selected.type}</span>
                  <span className="badge muted">degree {selected.degree}</span>
                  <span className="badge muted">{selected.mentions} mentions</span>
                  {selected.community_id &&
                    data.communities[selected.community_id]?.dirty && (
                      <span className="badge warn">▲ stale summary</span>
                    )}
                </div>
                <p className="hint">
                  This entity exists because {selected.mentions} chunk
                  {selected.mentions === 1 ? "" : "s"} still mention it. Remove the last one
                  and it is collected automatically.
                </p>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>relation</th>
                        <th>other entity</th>
                        <th className="num">support</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedEdges.map((edge) => {
                        const otherId = edge.source === selected.id ? edge.target : edge.source;
                        const other = data.nodes.find((node) => node.id === otherId);
                        return (
                          <tr key={edge.id}>
                            <td>
                              <code>{edge.type}</code>
                            </td>
                            <td>{other?.label ?? otherId.slice(0, 8)}</td>
                            <td className="num" title={edge.provenance.join(", ")}>
                              {edge.support}
                            </td>
                          </tr>
                        );
                      })}
                      {selectedEdges.length === 0 && (
                        <tr>
                          <td colSpan={3} className="empty">
                            No relations.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <p className="hint">
                Click a node to see its relations and how many chunks support each one. An
                edge with support 1 disappears the moment its single source document is
                deleted; an edge with support 2 survives, weakened.
              </p>
            )}
          </div>

          <div className="card">
            <h3>Graph at a glance</h3>
            <div className="grid cols-2">
              <div className="stat">
                <div className="label">entities</div>
                <div className="value">{data.nodes.length}</div>
              </div>
              <div className="stat">
                <div className="label">relations</div>
                <div className="value">{data.edges.length}</div>
              </div>
              <div className="stat">
                <div className="label">communities</div>
                <div className="value">{communities.length}</div>
              </div>
              <div className="stat">
                <div className="label">singly-supported</div>
                <div className="value">
                  {data.edges.filter((edge) => edge.support === 1).length}
                </div>
                <div className="sub">relations one delete would remove</div>
              </div>
            </div>
            {data.truncated && (
              <div className="notice" style={{ marginTop: 12 }}>
                Showing the highest-degree entities only — the graph is larger than the view
                limit.
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
