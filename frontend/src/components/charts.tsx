/**
 * Chart primitives, hand-rolled in SVG.
 *
 * Conventions held across all of them, per the data-viz method:
 *  - one y-axis, never two;
 *  - categorical colours assigned in fixed slot order and never cycled;
 *  - 2px lines, >=8px markers, 2px surface gaps between adjacent fills;
 *  - recessive grid and axes, primary ink for text (never the series colour);
 *  - a legend whenever there are >=2 series, plus direct labels where they fit;
 *  - a hover layer on every plot, and a table view behind a toggle - which is also the
 *    relief required for the two light-mode series colours that sit below 3:1 contrast.
 */

import { useId, useMemo, useState } from "react";

export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];

export interface TooltipState {
  x: number;
  y: number;
  title: string;
  rows: { label: string; value: string }[];
}

export function Tooltip({ state }: { state: TooltipState | null }) {
  if (!state) return null;
  return (
    <div
      className="tooltip"
      style={{ left: Math.min(state.x + 14, window.innerWidth - 300), top: state.y + 14 }}
      role="status"
    >
      <div className="t-title">{state.title}</div>
      {state.rows.map((row) => (
        <div className="t-row" key={row.label}>
          <span>{row.label}</span>
          <b>{row.value}</b>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  if (items.length < 2) return null;
  return (
    <div className="legend">
      {items.map((item) => (
        <span className="key" key={item.label}>
          <span className="swatch" style={{ background: item.color }} aria-hidden />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const scaled = value / magnitude;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * magnitude;
}

function format(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(2);
}

/** Wraps a chart with its title, an optional table view, and the toggle between them. */
export function ChartFrame({
  title,
  hint,
  table,
  children,
}: {
  title: string;
  hint?: string;
  table?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  return (
    <div className="card">
      <div className="chart-head">
        <div>
          <h3>{title}</h3>
          {hint && <p className="hint">{hint}</p>}
        </div>
        {table && (
          <button className="ghost small" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "chart" : "table"}
          </button>
        )}
      </div>
      {showTable && table ? <div className="table-wrap">{table}</div> : children}
    </div>
  );
}

// ---------------------------------------------------------------------------- line
export interface LinePoint {
  x: number;
  values: number[];
  label?: string;
}

export function LineChart({
  points,
  series,
  height = 200,
  yLabel,
  formatX,
}: {
  points: LinePoint[];
  series: string[];
  height?: number;
  yLabel?: string;
  formatX?: (x: number) => string;
}) {
  const [tip, setTip] = useState<TooltipState | null>(null);
  const clip = useId();
  const pad = { top: 10, right: 14, bottom: 24, left: 44 };
  const width = 620;
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  const max = useMemo(
    () => niceMax(Math.max(1, ...points.flatMap((p) => p.values))),
    [points],
  );

  if (points.length === 0) {
    return <div className="empty">No samples yet — the chart fills in as the index changes.</div>;
  }

  const xAt = (index: number) =>
    pad.left + (points.length === 1 ? innerW / 2 : (index / (points.length - 1)) * innerW);
  const yAt = (value: number) => pad.top + innerH - (value / max) * innerH;

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => max * t);

  return (
    <>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", height: "auto" }}
        role="img"
        aria-label={`${yLabel ?? "value"} over time`}
        onMouseLeave={() => setTip(null)}
      >
        <defs>
          <clipPath id={clip}>
            <rect x={pad.left} y={pad.top} width={innerW} height={innerH} />
          </clipPath>
        </defs>

        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={yAt(tick)}
              y2={yAt(tick)}
              stroke="var(--grid)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 7}
              y={yAt(tick) + 3.5}
              textAnchor="end"
              fontSize={10.5}
              fill="var(--text-muted)"
            >
              {format(tick)}
            </text>
          </g>
        ))}
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={pad.top + innerH}
          y2={pad.top + innerH}
          stroke="var(--axis)"
          strokeWidth={1}
        />

        <g clipPath={`url(#${clip})`}>
          {series.map((_, s) => (
            <polyline
              key={s}
              fill="none"
              stroke={SERIES[s % SERIES.length]}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
              points={points.map((p, i) => `${xAt(i)},${yAt(p.values[s] ?? 0)}`).join(" ")}
            />
          ))}
        </g>

        {/* Hover columns: hit targets far wider than the marks themselves. */}
        {points.map((point, index) => (
          <rect
            key={index}
            x={xAt(index) - innerW / Math.max(points.length, 1) / 2}
            y={pad.top}
            width={innerW / Math.max(points.length, 1)}
            height={innerH}
            fill="transparent"
            onMouseMove={(event) =>
              setTip({
                x: event.clientX,
                y: event.clientY,
                title: point.label ?? (formatX ? formatX(point.x) : String(point.x)),
                rows: series.map((name, s) => ({
                  label: name,
                  value: format(point.values[s] ?? 0),
                })),
              })
            }
          />
        ))}

        {tip && (
          <g>
            {points.map((point, index) =>
              series.map((_, s) => (
                <circle
                  key={`${index}-${s}`}
                  cx={xAt(index)}
                  cy={yAt(point.values[s] ?? 0)}
                  r={index === points.length - 1 ? 4 : 0}
                  fill={SERIES[s % SERIES.length]}
                  stroke="var(--surface-1)"
                  strokeWidth={2}
                />
              )),
            )}
          </g>
        )}

        {/* Always mark the latest value, so the current state is readable at a glance. */}
        {series.map((_, s) => (
          <circle
            key={`last-${s}`}
            cx={xAt(points.length - 1)}
            cy={yAt(points[points.length - 1].values[s] ?? 0)}
            r={4}
            fill={SERIES[s % SERIES.length]}
            stroke="var(--surface-1)"
            strokeWidth={2}
          />
        ))}
      </svg>
      <Legend items={series.map((label, s) => ({ label, color: SERIES[s % SERIES.length] }))} />
      <Tooltip state={tip} />
    </>
  );
}

// ---------------------------------------------------------------------------- bars
export function BarChart({
  data,
  height = 210,
  valueLabel = "value",
  colorIndex = 0,
}: {
  data: { label: string; value: number; note?: string }[];
  height?: number;
  valueLabel?: string;
  colorIndex?: number;
}) {
  const [tip, setTip] = useState<TooltipState | null>(null);
  const pad = { top: 10, right: 14, bottom: 46, left: 52 };
  const width = 620;
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(1, ...data.map((d) => d.value)));

  if (data.length === 0) return <div className="empty">Nothing to plot yet.</div>;

  const slot = innerW / data.length;
  const barW = Math.min(46, slot - 10); // the gap between bars is surface, not a stroke

  return (
    <>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", height: "auto" }}
        role="img"
        aria-label={valueLabel}
        onMouseLeave={() => setTip(null)}
      >
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={pad.top + innerH - t * innerH}
              y2={pad.top + innerH - t * innerH}
              stroke="var(--grid)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 7}
              y={pad.top + innerH - t * innerH + 3.5}
              textAnchor="end"
              fontSize={10.5}
              fill="var(--text-muted)"
            >
              {format(max * t)}
            </text>
          </g>
        ))}

        {data.map((item, index) => {
          const barH = (item.value / max) * innerH;
          const x = pad.left + index * slot + (slot - barW) / 2;
          const y = pad.top + innerH - barH;
          return (
            <g key={item.label}>
              {/* 4px rounded top, square foot: the bar stays anchored to the baseline. */}
              <path
                d={`M${x},${pad.top + innerH} L${x},${y + 4} Q${x},${y} ${x + 4},${y}
                    L${x + barW - 4},${y} Q${x + barW},${y} ${x + barW},${y + 4}
                    L${x + barW},${pad.top + innerH} Z`}
                fill={SERIES[colorIndex % SERIES.length]}
                onMouseMove={(event) =>
                  setTip({
                    x: event.clientX,
                    y: event.clientY,
                    title: item.label,
                    rows: [
                      { label: valueLabel, value: format(item.value) },
                      ...(item.note ? [{ label: "note", value: item.note }] : []),
                    ],
                  })
                }
              />
              <text
                x={x + barW / 2}
                y={y - 5}
                textAnchor="middle"
                fontSize={10.5}
                fill="var(--text-primary)"
                fontWeight={600}
              >
                {format(item.value)}
              </text>
              <text
                x={x + barW / 2}
                y={pad.top + innerH + 15}
                textAnchor="middle"
                fontSize={10.5}
                fill="var(--text-secondary)"
              >
                {item.label.length > 12 ? `${item.label.slice(0, 11)}…` : item.label}
              </text>
            </g>
          );
        })}
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={pad.top + innerH}
          y2={pad.top + innerH}
          stroke="var(--axis)"
          strokeWidth={1}
        />
      </svg>
      <Tooltip state={tip} />
    </>
  );
}

// ------------------------------------------------------------------- stacked bars
export function StackedBar({
  segments,
  total,
}: {
  segments: { label: string; value: number }[];
  total?: number;
}) {
  const sum = total ?? segments.reduce((acc, s) => acc + s.value, 0);
  if (sum <= 0) return <div className="empty">No spend recorded yet.</div>;
  return (
    <>
      <div
        style={{ display: "flex", gap: 2, height: 26, borderRadius: 6, overflow: "hidden" }}
        role="img"
        aria-label="composition"
      >
        {segments
          .filter((s) => s.value > 0)
          .map((segment, index) => (
            <div
              key={segment.label}
              title={`${segment.label}: ${format(segment.value)}`}
              style={{
                width: `${(segment.value / sum) * 100}%`,
                background: SERIES[index % SERIES.length],
              }}
            />
          ))}
      </div>
      <Legend
        items={segments
          .filter((s) => s.value > 0)
          .map((segment, index) => ({
            label: `${segment.label} · ${format(segment.value)}`,
            color: SERIES[index % SERIES.length],
          }))}
      />
    </>
  );
}

// ---------------------------------------------------------------------------- scatter
export interface ScatterPoint {
  x: number;
  y: number;
  label: string;
  group: number;
  note?: string;
}

export function ScatterChart({
  points,
  xLabel,
  yLabel,
  height = 260,
}: {
  points: ScatterPoint[];
  xLabel: string;
  yLabel: string;
  height?: number;
}) {
  const [tip, setTip] = useState<TooltipState | null>(null);
  const pad = { top: 14, right: 90, bottom: 40, left: 56 };
  const width = 620;
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  if (points.length === 0) return <div className="empty">Run the eval to populate this curve.</div>;

  const maxX = niceMax(Math.max(...points.map((p) => p.x)));
  const maxY = niceMax(Math.max(0.001, ...points.map((p) => p.y)));
  const xAt = (v: number) => pad.left + (v / maxX) * innerW;
  const yAt = (v: number) => pad.top + innerH - (v / maxY) * innerH;

  return (
    <>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", height: "auto" }}
        role="img"
        aria-label={`${yLabel} against ${xLabel}`}
        onMouseLeave={() => setTip(null)}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={yAt(maxY * t)}
              y2={yAt(maxY * t)}
              stroke="var(--grid)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 7}
              y={yAt(maxY * t) + 3.5}
              textAnchor="end"
              fontSize={10.5}
              fill="var(--text-muted)"
            >
              {format(maxY * t)}
            </text>
            <text
              x={xAt(maxX * t)}
              y={height - 22}
              textAnchor="middle"
              fontSize={10.5}
              fill="var(--text-muted)"
            >
              {format(maxX * t)}
            </text>
          </g>
        ))}

        {/* The frontier line: sort by cost and connect, so the trade-off reads as a curve. */}
        <polyline
          fill="none"
          stroke="var(--axis)"
          strokeWidth={1}
          strokeDasharray="3 3"
          points={points
            .filter((p) => p.group === 0)
            .sort((a, b) => a.x - b.x)
            .map((p) => `${xAt(p.x)},${yAt(p.y)}`)
            .join(" ")}
        />

        {points.map((point) => (
          <g key={point.label}>
            <circle
              cx={xAt(point.x)}
              cy={yAt(point.y)}
              r={7}
              fill={SERIES[point.group % SERIES.length]}
              stroke="var(--surface-1)"
              strokeWidth={2}
              onMouseMove={(event) =>
                setTip({
                  x: event.clientX,
                  y: event.clientY,
                  title: point.label,
                  rows: [
                    { label: xLabel, value: format(point.x) },
                    { label: yLabel, value: format(point.y) },
                    ...(point.note ? [{ label: "", value: point.note }] : []),
                  ],
                })
              }
            />
            {/* Direct labels: also the relief for the sub-3:1 light-mode series colours. */}
            <text
              x={xAt(point.x) + 11}
              y={yAt(point.y) + 3.5}
              fontSize={10.5}
              fill="var(--text-secondary)"
            >
              {point.label}
            </text>
          </g>
        ))}

        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={pad.top + innerH}
          y2={pad.top + innerH}
          stroke="var(--axis)"
          strokeWidth={1}
        />
        <text
          x={pad.left + innerW / 2}
          y={height - 5}
          textAnchor="middle"
          fontSize={11}
          fill="var(--text-secondary)"
        >
          {xLabel}
        </text>
        <text
          x={-(pad.top + innerH / 2)}
          y={13}
          transform="rotate(-90)"
          textAnchor="middle"
          fontSize={11}
          fill="var(--text-secondary)"
        >
          {yLabel}
        </text>
      </svg>
      <Tooltip state={tip} />
    </>
  );
}

export { format as formatNumber };
