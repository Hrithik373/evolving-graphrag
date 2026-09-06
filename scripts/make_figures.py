"""Render the report figures from an eval run.

Reads ``results/summary.json`` and writes SVG figures. Deliberately dependency-free: it
emits SVG directly rather than requiring matplotlib, so ``make figures`` works in the same
environment as everything else.

Colours follow the project's validated data-viz palette; every figure carries direct
labels and ships alongside the CSV it was drawn from, which is the required relief for the
two light-mode series colours that sit below 3:1 contrast on the chart surface.

    python scripts/make_figures.py --results results
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Categorical slots, fixed order, never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK = "#0b0b0b"
INK_SOFT = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

W, H = 720, 420
PAD = {"top": 54, "right": 34, "bottom": 62, "left": 78}


def _nice(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** int(f"{value:e}".split("e")[1])
    scaled = value / magnitude
    step = 1 if scaled <= 1 else 2 if scaled <= 2 else 5 if scaled <= 5 else 10
    return step * magnitude


def _fmt(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _open(title: str, subtitle: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f"font-family='{FONT}'>",
        f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
        f'<text x="{PAD["left"]}" y="26" font-size="15" font-weight="600" fill="{INK}">{title}</text>',
        f'<text x="{PAD["left"]}" y="44" font-size="11.5" fill="{INK_SOFT}">{subtitle}</text>',
    ]


def _axes(parts: list[str], max_y: float, y_label: str, x_label: str) -> None:
    inner_h = H - PAD["top"] - PAD["bottom"]
    for tick in range(5):
        fraction = tick / 4
        y = PAD["top"] + inner_h - fraction * inner_h
        parts.append(
            f'<line x1="{PAD["left"]}" x2="{W - PAD["right"]}" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD["left"] - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10.5" '
            f'fill="{MUTED}">{_fmt(max_y * fraction)}</text>'
        )
    parts.append(
        f'<line x1="{PAD["left"]}" x2="{W - PAD["right"]}" y1="{PAD["top"] + inner_h}" '
        f'y2="{PAD["top"] + inner_h}" stroke="{AXIS}" stroke-width="1"/>'
    )
    parts.append(
        f'<text x="{PAD["left"] - 58}" y="{PAD["top"] + inner_h / 2}" font-size="11" '
        f'fill="{INK_SOFT}" transform="rotate(-90 {PAD["left"] - 58} {PAD["top"] + inner_h / 2})" '
        f'text-anchor="middle">{y_label}</text>'
    )
    parts.append(
        f'<text x="{PAD["left"] + (W - PAD["left"] - PAD["right"]) / 2}" y="{H - 12}" '
        f'text-anchor="middle" font-size="11" fill="{INK_SOFT}">{x_label}</text>'
    )


def figure_pareto(points: list[dict], out: Path) -> Path:
    """The headline: update cost against reader-visible staleness."""
    inner_w = W - PAD["left"] - PAD["right"]
    inner_h = H - PAD["top"] - PAD["bottom"]
    max_x = _nice(max(p["update_tokens"] for p in points))
    max_y = _nice(max(0.01, max(p["mean_stale_touched"] for p in points)))

    parts = _open(
        "Cost vs freshness",
        "Update cost against the stale summaries a query actually reads. "
        "Full reindex buys the same freshness for several times the tokens.",
    )
    _axes(parts, max_y, "stale summaries per query", "update tokens (churn + deletion phase)")

    def x_at(v: float) -> float:
        return PAD["left"] + (v / max_x) * inner_w

    def y_at(v: float) -> float:
        return PAD["top"] + inner_h - (v / max_y) * inner_h

    for tick in range(5):
        value = max_x * tick / 4
        parts.append(
            f'<text x="{x_at(value):.1f}" y="{H - 34}" text-anchor="middle" font-size="10.5" '
            f'fill="{MUTED}">{_fmt(value)}</text>'
        )

    curve = sorted(
        (p for p in points if p["label"].startswith("evolving")), key=lambda p: p["update_tokens"]
    )
    if len(curve) > 1:
        path = " ".join(
            f"{x_at(p['update_tokens']):.1f},{y_at(p['mean_stale_touched']):.1f}" for p in curve
        )
        parts.append(
            f'<polyline points="{path}" fill="none" stroke="{AXIS}" stroke-width="1" '
            f'stroke-dasharray="4 3"/>'
        )

    for point in points:
        reference = not point["label"].startswith("evolving")
        colour = SERIES[1] if reference else SERIES[0]
        x, y = x_at(point["update_tokens"]), y_at(point["mean_stale_touched"])
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colour}" stroke="{SURFACE}" stroke-width="2"/>'
        )
        label = point["label"].replace("evolving/", "")
        anchor = "end" if x > W - PAD["right"] - 110 else "start"
        offset = -12 if anchor == "end" else 12
        parts.append(
            f'<text x="{x + offset:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}" font-size="10.5" '
            f'fill="{INK}">{label}</text>'
        )

    parts.append(
        f'<g transform="translate({PAD["left"]},{H - 50})" font-size="11" fill="{INK_SOFT}">'
        f'<circle cx="5" cy="-4" r="5" fill="{SERIES[0]}"/><text x="16" y="0">this system</text>'
        f'<circle cx="115" cy="-4" r="5" fill="{SERIES[1]}"/><text x="126" y="0">full reindex</text></g>'
    )
    parts.append("</svg>")
    path = out / "fig_pareto.svg"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def figure_scaling(rows: list[dict], out: Path) -> Path:
    """Update cost stays flat while rebuild cost grows with the corpus."""
    inner_w = W - PAD["left"] - PAD["right"]
    inner_h = H - PAD["top"] - PAD["bottom"]
    max_y = _nice(max(r["build_tokens"] for r in rows))

    parts = _open(
        "Update cost vs corpus size",
        "One document change costs the same at every corpus size; rebuilding does not. "
        "That gap is the asymptotic form of the argument.",
    )
    _axes(parts, max_y, "tokens", "documents in the corpus")

    slot = inner_w / max(len(rows), 1)
    for index, row in enumerate(rows):
        centre = PAD["left"] + slot * (index + 0.5)
        for offset, key, colour in (
            (-0.22, "build_tokens", SERIES[1]),
            (0.22, "one_update_tokens", SERIES[0]),
        ):
            value = row[key]
            bar_w = slot * 0.34
            x = centre + slot * offset - bar_w / 2
            bar_h = (value / max_y) * inner_h
            y = PAD["top"] + inner_h - bar_h
            parts.append(
                f'<path d="M{x:.1f},{PAD["top"] + inner_h} L{x:.1f},{y + 4:.1f} '
                f"Q{x:.1f},{y:.1f} {x + 4:.1f},{y:.1f} L{x + bar_w - 4:.1f},{y:.1f} "
                f"Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + 4:.1f} "
                f'L{x + bar_w:.1f},{PAD["top"] + inner_h} Z" fill="{colour}"/>'
            )
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" '
                f'font-size="10" font-weight="600" fill="{INK}">{_fmt(value)}</text>'
            )
        parts.append(
            f'<text x="{centre:.1f}" y="{PAD["top"] + inner_h + 17:.1f}" text-anchor="middle" '
            f'font-size="10.5" fill="{INK_SOFT}">{row["documents"]}</text>'
        )
        parts.append(
            f'<text x="{centre:.1f}" y="{PAD["top"] + inner_h + 31:.1f}" text-anchor="middle" '
            f'font-size="10" fill="{MUTED}">{row["reindex_cost_ratio"]}x</text>'
        )

    parts.append(
        f'<g transform="translate({PAD["left"]},{H - 46})" font-size="11" fill="{INK_SOFT}">'
        f'<rect x="0" y="-9" width="10" height="10" rx="3" fill="{SERIES[1]}"/>'
        f'<text x="16" y="0">full rebuild</text>'
        f'<rect x="110" y="-9" width="10" height="10" rx="3" fill="{SERIES[0]}"/>'
        f'<text x="126" y="0">one incremental update</text></g>'
    )
    parts.append("</svg>")
    path = out / "fig_scaling.svg"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def figure_deletion(scenarios: list[dict], out: Path) -> Path:
    """Stale-answer rate after deletion, by system."""
    rows = [s for s in scenarios if s["scenario"] == "deletion"]
    if not rows:
        return out / "fig_deletion.svg"
    inner_w = W - PAD["left"] - PAD["right"]
    inner_h = H - PAD["top"] - PAD["bottom"]

    parts = _open(
        "Stale answers after deletion",
        "Questions still answered with a fact only a deleted document supported. "
        "Lower is better; zero is correct.",
    )
    _axes(parts, 1.0, "stale-answer rate", "system")

    slot = inner_w / max(len(rows), 1)
    for index, row in enumerate(rows):
        value = row["stale_answer_rate"]
        bar_w = min(72, slot - 26)
        x = PAD["left"] + slot * index + (slot - bar_w) / 2
        bar_h = max(value * inner_h, 1.5)
        y = PAD["top"] + inner_h - bar_h
        colour = "#0ca30c" if value == 0 else "#d03b3b" if value >= 0.5 else "#ec835a"
        parts.append(
            f'<path d="M{x:.1f},{PAD["top"] + inner_h} L{x:.1f},{y + 4:.1f} '
            f"Q{x:.1f},{y:.1f} {x + 4:.1f},{y:.1f} L{x + bar_w - 4:.1f},{y:.1f} "
            f"Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + 4:.1f} "
            f'L{x + bar_w:.1f},{PAD["top"] + inner_h} Z" fill="{colour}"/>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="11" '
            f'font-weight="600" fill="{INK}">{value:.2f}</text>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{PAD["top"] + inner_h + 18:.1f}" text-anchor="middle" '
            f'font-size="10.5" fill="{INK_SOFT}">{row["system"]}</text>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{PAD["top"] + inner_h + 32:.1f}" text-anchor="middle" '
            f'font-size="10" fill="{MUTED}">{_fmt(row["update_tokens"])} tok</text>'
        )
    parts.append("</svg>")
    path = out / "fig_deletion.svg"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="results")
    args = parser.parse_args()

    out = Path(args.results)
    summary_path = out / "summary.json"
    if not summary_path.exists():
        print(f"no {summary_path} — run `make eval` first")
        return 1

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    written = [
        figure_pareto(summary["pareto"]["points"], out),
        figure_scaling(summary["scaling"], out),
        figure_deletion(
            [
                {
                    "system": s["system"],
                    "scenario": s["scenario"],
                    "stale_answer_rate": s["stale_answer_rate"],
                    "update_tokens": s["update_cost"]["tokens"],
                }
                for s in summary["scenarios"]
            ],
            out,
        ),
    ]
    for path in written:
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
