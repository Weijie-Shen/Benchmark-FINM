#!/usr/bin/env python3
"""Generate report figures from one or more details_*.json files.

By default, loads all `results/details_run2_judge_*.json` files and produces:
  1. leaderboard.png         — horizontal bar with judge spread (min/max)
  2. heatmap_categories.png  — model × category accuracy heatmap
  3. category_spread.png     — per-category cross-judge spread bars
  4. rubric_spread.png       — per-cell rubric score spread (top-N by spread)
  5. agreement_breakdown.png — N-way verdict agreement + split-pattern bars

The script is N-judge agnostic: pass any number of `details_*.json` files
and it scales. Judge labels are inferred from filename
(`details_run2_judge_<name>.json` → `<name>`), or override with `path:label`.

Usage:
    # Default: globs results/details_run2_judge_*.json
    python make_report.py

    # Explicit inputs (label inferred from filename):
    python make_report.py --inputs results/details_run2_judge_gemini.json \\
                                   results/details_run2_judge_deepseek.json

    # With explicit label:
    python make_report.py --inputs results/details_run2_judge_gemini.json:gem \\
                                   results/foo.json:custom-judge

    # Custom output dir + top-N rubric cells:
    python make_report.py --out figures/ --top-rubric 50
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def parse_spec(spec: str) -> tuple[Path, str]:
    """Parse 'path' or 'path:label'. Returns (Path, label).

    Filename label inference (in order):
      - `details_*_judge_<name>.json` → <name>  (old run2 convention)
      - `details_<run>_<name>.json`   → <name>  (run3+ convention)
      - `details_<name>.json`         → <name>
    """
    # `path:label` form, but be careful about Windows paths like `C:\foo`
    if ":" in spec and not (len(spec) > 2 and spec[1] == ":"):
        path_s, _, label = spec.rpartition(":")
        return Path(path_s), label
    p = Path(spec)
    label = p.stem
    if "_judge_" in label:
        label = label.rsplit("_judge_", 1)[1]
    elif label.startswith("details_"):
        parts = label[len("details_"):].split("_")
        label = parts[-1] if len(parts) >= 2 else parts[0]
    return p, label


def load_judges(specs: list[str]) -> dict[str, list[dict]]:
    """{label: rows}"""
    out: dict[str, list[dict]] = {}
    for s in specs:
        p, label = parse_spec(s)
        if not p.exists():
            raise SystemExit(f"input not found: {p}")
        rows = json.load(open(p))
        if label in out:
            raise SystemExit(f"duplicate judge label {label!r}; "
                             f"use 'path:label' to disambiguate")
        out[label] = rows
        print(f"  loaded {len(rows):>4} cells as judge '{label}' from {p}")
    return out


def load_topics(questions_dir: Path) -> dict[str, str]:
    """{question_id: topic}, by walking *.json files in `questions_dir`."""
    topics: dict[str, str] = {}
    for f in sorted(questions_dir.glob("*.json")):
        for q in json.load(open(f)):
            topics[q["id"]] = q["topic"]
    return topics


def index_cells(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """{(model, qid): row}"""
    return {(r["model"], r["question_id"]): r for r in rows}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_ACC_COLORS = ["#c62828", "#ef6c00", "#0288d1", "#2e7d32"]  # red / orange / blue / green


def _acc_color(acc: float) -> str:
    """Quartile-bucket color for an accuracy in [0,1]."""
    if acc < 0.25: return _ACC_COLORS[0]
    if acc < 0.50: return _ACC_COLORS[1]
    if acc < 0.75: return _ACC_COLORS[2]
    return _ACC_COLORS[3]


def _judge_palette(n: int):
    """A categorical color palette for `n` judges."""
    return plt.cm.Set2(np.linspace(0, 0.8, max(n, 2)))


# ---------------------------------------------------------------------------
# Chart 1 — leaderboard
# ---------------------------------------------------------------------------
def chart_leaderboard(judges: dict, out_path: Path,
                      canonical: str | None = None) -> None:
    """Horizontal bar of accuracy per model. If `canonical` is set, shows
    that judge's scores as solid bars (no error bars). Otherwise shows
    median across all judges with min/max error bars."""
    models = sorted({r["model"] for rows in judges.values() for r in rows})
    accs: dict[str, dict[str, float]] = {m: {} for m in models}
    for label, rows in judges.items():
        per_model: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
        for r in rows:
            per_model[r["model"]][0] += r["score"]
            per_model[r["model"]][1] += 1
        for m, (s, n) in per_model.items():
            accs[m][label] = (s / n) if n else 0.0

    if canonical is not None:
        if canonical not in judges:
            raise SystemExit(f"--canonical-judge {canonical!r} not in loaded judges: "
                             f"{sorted(judges)}")
        center = {m: accs[m][canonical] for m in models}
        title = f"Leaderboard — accuracy by model (judge={canonical})"
    else:
        center = {m: float(np.median(list(accs[m].values()))) for m in models}
        title = (f"Leaderboard — median accuracy with min/max across "
                 f"{len(judges)} judges ({', '.join(judges.keys())})")

    models = sorted(models, key=lambda m: center[m])  # asc → matplotlib puts largest on top
    center_vals = np.array([center[m] for m in models])

    fig, ax = plt.subplots(figsize=(11, max(5.5, 0.55 * len(models))))
    y = np.arange(len(models))
    colors = [_acc_color(v) for v in center_vals]

    if canonical is None:
        mins = np.array([min(accs[m].values()) for m in models])
        maxs = np.array([max(accs[m].values()) for m in models])
        err = np.vstack([center_vals - mins, maxs - center_vals])
        ax.barh(y, center_vals, color=colors, edgecolor="black", linewidth=0.5,
                xerr=err,
                error_kw={"linewidth": 1.2, "capsize": 4,
                          "ecolor": "#222", "capthick": 1.2})
        for i, (mv, lo, hi) in enumerate(zip(center_vals, mins, maxs)):
            txt = f"{mv:.0%}" if lo == hi else f"{mv:.0%}  (range {lo:.0%}–{hi:.0%})"
            ax.text(hi + 0.01, i, txt, va="center", fontsize=9)
    else:
        ax.barh(y, center_vals, color=colors, edgecolor="black", linewidth=0.5)
        for i, mv in enumerate(center_vals):
            ax.text(mv + 0.01, i, f"{mv:.0%}", va="center", fontsize=9)

    ax.set_yticks(y)
    ax.set_yticklabels(models)
    ax.set_xlabel("Accuracy")
    ax.set_xlim(0, 1.05)
    ax.xaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.set_title(title)
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 2 — model × category heatmap
# ---------------------------------------------------------------------------
def chart_heatmap(judges: dict, topics: dict, out_path: Path,
                  canonical: str | None = None) -> None:
    """Accuracy per (model, category) heatmap. Uses `canonical` judge's
    scores if given, else median across all judges."""
    if canonical is not None and canonical not in judges:
        raise SystemExit(f"--canonical-judge {canonical!r} not in loaded judges: "
                         f"{sorted(judges)}")
    cells_by_judge = {label: index_cells(rows) for label, rows in judges.items()}
    if canonical is not None:
        cells_by_judge = {canonical: cells_by_judge[canonical]}
    models = sorted({r["model"] for rows in judges.values() for r in rows})
    cats = sorted({
        topics[r["question_id"]]
        for rows in judges.values()
        for r in rows
        if r["question_id"] in topics
    })

    grid = np.zeros((len(models), len(cats)))
    for i, m in enumerate(models):
        for j, c in enumerate(cats):
            per_judge_acc = []
            for idx in cells_by_judge.values():
                cells = [
                    r for k, r in idx.items()
                    if k[0] == m and topics.get(k[1]) == c
                ]
                if not cells:
                    continue
                per_judge_acc.append(sum(r["score"] for r in cells) / len(cells))
            grid[i, j] = float(np.median(per_judge_acc)) if per_judge_acc else 0.0

    overall = grid.mean(axis=1)
    order = np.argsort(-overall)
    grid = grid[order]
    models = [models[i] for i in order]

    fig, ax = plt.subplots(figsize=(2 + 1.4 * len(cats), 1.5 + 0.5 * len(models)))
    im = ax.imshow(grid, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(cats)))
    ax.set_xticklabels(cats, rotation=25, ha="right")
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels(models)
    for i in range(len(models)):
        for j in range(len(cats)):
            v = grid[i, j]
            color = "white" if v < 0.35 or v > 0.85 else "black"
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=9, color=color)
    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    if canonical is not None:
        cbar.set_label(f"Accuracy (judge={canonical})")
        ax.set_title(f"Per-category accuracy by model (judge={canonical})")
    else:
        cbar.set_label("Accuracy (median across judges)")
        ax.set_title("Per-category accuracy by model")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 3 — per-category spread bar chart
# ---------------------------------------------------------------------------
def chart_category_spread(judges: dict, topics: dict, out_path: Path) -> None:
    """Total points per (judge, category), grouped bars; spread Δ annotated."""
    cats = sorted({
        topics[r["question_id"]]
        for rows in judges.values()
        for r in rows
        if r["question_id"] in topics
    })
    per_judge_cat: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for label, rows in judges.items():
        for r in rows:
            c = topics.get(r["question_id"])
            if c:
                per_judge_cat[label][c] += r["score"]

    judge_labels = list(judges.keys())
    spreads = []
    for c in cats:
        vals = [per_judge_cat[j][c] for j in judge_labels]
        spreads.append((c, vals, max(vals) - min(vals)))
    spreads.sort(key=lambda x: -x[2])

    fig, ax = plt.subplots(figsize=(max(8, 1.4 * len(cats) + 2), 5.5))
    x = np.arange(len(cats))
    n = len(judges)
    width = 0.8 / n
    colors = _judge_palette(n)
    for k, label in enumerate(judge_labels):
        vals = [per_judge_cat[label][c] for c, _, _ in spreads]
        ax.bar(
            x + k * width - 0.4 + width / 2, vals, width,
            label=label, color=colors[k], edgecolor="black", linewidth=0.5,
        )
    for i, (_, vals, sp) in enumerate(spreads):
        ax.text(i, max(vals) + 1.5, f"Δ={sp:.1f}", ha="center", fontsize=9, weight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([c for c, _, _ in spreads], rotation=15, ha="right")
    ax.set_ylabel("Total points (all models)")
    ax.set_title("Per-category cross-judge spread (sorted by Δ descending)")
    ax.legend(title="judge")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 4 — rubric spread plot
# ---------------------------------------------------------------------------
def chart_rubric_spread(
    judges: dict, out_path: Path, top_n: int = 30
) -> None:
    """For each rubric cell present in all judges, plot min-max bar + per-judge
    dots. Sorted by spread descending; show top_n."""
    cells: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for label, rows in judges.items():
        for r in rows:
            if r.get("rubric_score") is None:
                continue
            cells[(r["model"], r["question_id"])][label] = r["rubric_score"]
    full = {k: v for k, v in cells.items() if len(v) == len(judges)}
    if not full:
        print("  no rubric cells with all judges present — skipping rubric_spread")
        return

    items = [((m, q), s, max(s.values()) - min(s.values())) for (m, q), s in full.items()]
    items.sort(key=lambda x: -x[2])
    items = items[:top_n]

    judge_labels = list(judges.keys())
    colors = _judge_palette(len(judge_labels))

    fig, ax = plt.subplots(figsize=(max(11, 0.45 * len(items) + 2), 6.5))
    for i, ((m, q), scores, _sp) in enumerate(items):
        vals = [scores[j] for j in judge_labels]
        ax.vlines(i, min(vals), max(vals), color="grey", lw=1.0, alpha=0.5, zorder=1)
        for k, j in enumerate(judge_labels):
            ax.scatter(
                i, scores[j], s=55, color=colors[k],
                label=j if i == 0 else None,
                edgecolor="black", linewidth=0.5, zorder=3,
            )

    ax.set_xticks(range(len(items)))
    ax.set_xticklabels([f"{m}\n{q}" for ((m, q), _, _) in items], rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("Rubric score (out of 10)")
    ax.set_ylim(-0.5, 10.5)
    ax.set_title(f"Per-cell rubric score spread across {len(judges)} judges — top {len(items)} cells by spread")
    ax.legend(title="judge", loc="upper right")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Chart 5 — agreement breakdown
# ---------------------------------------------------------------------------
def chart_agreement(judges: dict, out_path: Path) -> None:
    """Unanimous-YES / unanimous-NO / split breakdown + split-pattern bars."""
    if len(judges) < 2:
        print("  need ≥ 2 judges for agreement breakdown — skipping")
        return

    cells_by_judge = {label: index_cells(rows) for label, rows in judges.items()}
    common = set.intersection(*[set(idx.keys()) for idx in cells_by_judge.values()])
    judge_labels = list(judges.keys())

    binary_keys = []
    for k in common:
        cells = [cells_by_judge[j][k] for j in judge_labels]
        if any(c.get("correct") is None for c in cells):
            continue
        if len({c.get("raw_response") for c in cells}) > 1:
            continue  # raw mismatched — comparison not meaningful
        binary_keys.append(k)
    if not binary_keys:
        print("  no comparable binary cells across all judges — skipping")
        return

    patterns: Counter = Counter()
    for k in binary_keys:
        pat = tuple("Y" if cells_by_judge[j][k]["correct"] else "N" for j in judge_labels)
        patterns[pat] += 1

    all_y = patterns.get(("Y",) * len(judges), 0)
    all_n = patterns.get(("N",) * len(judges), 0)
    splits = sum(v for pat, v in patterns.items() if len(set(pat)) > 1)
    total = len(binary_keys)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), gridspec_kw={"width_ratios": [1, 2]})

    # LEFT: stacked vertical bar (unanimous YES / NO / split)
    ax = axes[0]
    counts = [all_y, all_n, splits]
    labels = [
        f"Unanimous YES ({all_y}/{total} = {all_y/total:.0%})",
        f"Unanimous NO  ({all_n}/{total} = {all_n/total:.0%})",
        f"Split         ({splits}/{total} = {splits/total:.0%})",
    ]
    colors = ["#2e7d32", "#c62828", "#ef6c00"]
    bottoms = [0, all_y, all_y + all_n]
    for c, h, b, lab in zip(colors, counts, bottoms, labels):
        ax.bar([0], [h], bottom=[b], color=c, label=lab, edgecolor="black", linewidth=0.5)
    ax.set_xticks([])
    ax.set_ylabel(f"Binary cells (N={total})")
    ax.set_title(f"{len(judges)}-judge verdict agreement")
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=9, frameon=False)

    # RIGHT: split-pattern breakdown
    ax = axes[1]
    split_patterns = sorted(
        [(pat, v) for pat, v in patterns.items() if len(set(pat)) > 1],
        key=lambda x: -x[1],
    )
    pat_labels = [
        " | ".join(f"{j}={p}" for j, p in zip(judge_labels, pat))
        for pat, _ in split_patterns
    ]
    pat_counts = [v for _, v in split_patterns]
    if pat_counts:
        bars = ax.barh(
            range(len(pat_counts)), pat_counts,
            color="#ef6c00", edgecolor="black", linewidth=0.5,
        )
        ax.set_yticks(range(len(pat_counts)))
        ax.set_yticklabels(pat_labels, fontsize=9)
        ax.invert_yaxis()
        for b, c in zip(bars, pat_counts):
            ax.text(b.get_width() + max(pat_counts) * 0.01, b.get_y() + b.get_height() / 2,
                    str(c), va="center", fontsize=9)
    ax.set_xlabel("Cell count")
    ax.set_title("Split-verdict patterns (sorted by frequency)")
    ax.grid(axis="x", linestyle=":", alpha=0.4)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(
        description="Generate cross-judge report figures from details_*.json files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--inputs", nargs="+",
        help="paths to details_*.json files. Format: 'path' or 'path:label'. "
             "Default: all results/details_run2_judge_*.json",
    )
    p.add_argument(
        "--questions", default="data",
        help="path to questions JSON dir (for topic mapping). Default: data/",
    )
    p.add_argument(
        "--out", default="report_figures",
        help="output directory for PNGs. Default: report_figures/",
    )
    p.add_argument(
        "--top-rubric", type=int, default=30,
        help="number of rubric cells to show in the spread plot "
             "(sorted by spread desc). Default: 30",
    )
    p.add_argument(
        "--canonical-judge",
        help="name of the judge to use as the SINGLE source for the "
             "leaderboard + heatmap (headline charts). The comparison "
             "charts (category_spread, rubric_spread, agreement_breakdown) "
             "always use all loaded judges. Default: median across all judges.",
    )
    args = p.parse_args()

    if not args.inputs:
        # Auto-discover the most recent run by looking for both naming
        # conventions: old `details_run<N>_judge_<judge>.json` (run2 era)
        # and new `details_run<N>_<judge>.json` (run3+).
        all_files = sorted(Path("results").glob("details_run*_*.json"))
        # Prefer the highest run number when both exist.
        if all_files:
            def run_num(p):
                stem = p.stem
                if stem.startswith("details_run"):
                    rest = stem[len("details_run"):].split("_", 1)[0]
                    try:
                        return int(rest)
                    except ValueError:
                        return 0
                return 0
            max_run = max(run_num(p) for p in all_files)
            args.inputs = [str(p) for p in all_files if run_num(p) == max_run]
        if not args.inputs:
            raise SystemExit(
                "no --inputs given and no results/details_run*_*.json found"
            )

    print(f"\n[make_report] loading {len(args.inputs)} judge file(s)...")
    judges = load_judges(args.inputs)

    print(f"[make_report] loading question topics from {args.questions}/...")
    topics = load_topics(Path(args.questions))
    print(f"  loaded {len(topics)} questions in {len(set(topics.values()))} categories")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[make_report] writing figures to {out_dir}/\n")

    canon = args.canonical_judge
    if canon:
        print(f"[make_report] using canonical judge for headline charts: {canon}")

    print("  [1/5] leaderboard.png")
    chart_leaderboard(judges, out_dir / "leaderboard.png", canonical=canon)
    print("  [2/5] heatmap_categories.png")
    chart_heatmap(judges, topics, out_dir / "heatmap_categories.png", canonical=canon)
    print("  [3/5] category_spread.png")
    chart_category_spread(judges, topics, out_dir / "category_spread.png")
    print("  [4/5] rubric_spread.png")
    chart_rubric_spread(judges, out_dir / "rubric_spread.png", top_n=args.top_rubric)
    print("  [5/5] agreement_breakdown.png")
    chart_agreement(judges, out_dir / "agreement_breakdown.png")

    n = len(list(out_dir.glob("*.png")))
    print(f"\n[make_report] done — {n} PNG(s) in {out_dir}/")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
