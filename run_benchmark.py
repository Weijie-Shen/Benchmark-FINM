#!/usr/bin/env python3
"""Entry point for the quant interview benchmark.

Usage:
    python run_benchmark.py
    python run_benchmark.py --questions other.json --out results/run1 --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

# Load .env early so has_api_key() sees OPENROUTER_API_KEY.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline.runner import run_benchmark


_USE_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


_ANSI = re.compile(r"\033\[[\d;]*m")


def _vlen(s: str) -> int:
    """Visible length of `s` after stripping ANSI escapes."""
    return len(_ANSI.sub("", s))


def _rpad(s: str, width: int) -> str:
    """Right-align `s` in a field of visible width `width`, accounting for ANSI."""
    pad = max(0, width - _vlen(s))
    return " " * pad + s


def _bar(score: float, total: float, width: int = 12) -> str:
    """ASCII bar showing score/total using half-block resolution."""
    if total <= 0:
        return " " * width
    frac = max(0.0, min(score / total, 1.0))
    cells_full = int(frac * width)
    has_half = (frac * width) - cells_full >= 0.5
    bar = "█" * cells_full + ("▌" if has_half and cells_full < width else "")
    return bar.ljust(width, "░")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--questions", default="data",
                   help="path to questions JSON file OR directory of files "
                        "(default: data/ — loads all *.json in it)")
    p.add_argument("--out", default="results",
                   help="output directory (default: results/)")
    p.add_argument("--concurrency", type=int, default=8,
                   help="max in-flight API calls (default: 8)")
    p.add_argument("--label",
                   help="suffix for output files, e.g. 'run2' -> details_run2.json. "
                        "Default: timestamp.")
    args = p.parse_args()

    questions_path = Path(args.questions)
    if not questions_path.exists():
        print(f"error: {questions_path} not found", file=sys.stderr)
        return 1

    summary = asyncio.run(run_benchmark(
        questions_path=questions_path,
        out_dir=Path(args.out),
        concurrency=args.concurrency,
        label=args.label,
    ))

    # ------------------------------------------------------------------
    # Leaderboard with bar chart
    # ------------------------------------------------------------------
    print()
    print(_c("=" * 78, "1"))
    print(_c(" Leaderboard", "1"))
    print(_c("=" * 78, "1"))
    rows = sorted(summary["per_model"].items(), key=lambda kv: -kv[1]["score_total"])
    n = summary["max_possible_score"]
    width = max(len(name) for name, _ in rows)

    print(f"  {'model':<{width}}   {'score':>6}   {'acc':>5}  err  chart                cost")
    for name, t in rows:
        sampling = "" if t["sampling_controlled"] else _c(" ⚠", "93")
        bar = _bar(t["score_total"], n, width=20)
        score_str = f"{t['score_total']:>5.2f}/{int(n):<2}"
        acc_str = f"{t['accuracy']:>5.1%}"
        errs = t["errors"]
        cost_str = f"${t['total_cost_usd']:>6.3f}"
        # Color the bar by quartile
        if t["accuracy"] >= 0.75:
            bar_c = _c(bar, "92")
        elif t["accuracy"] >= 0.5:
            bar_c = _c(bar, "96")
        elif t["accuracy"] >= 0.25:
            bar_c = _c(bar, "93")
        else:
            bar_c = _c(bar, "91")
        print(f"  {name:<{width}}{sampling}  {score_str}  {acc_str}  {errs:>3}  {bar_c}  {cost_str}")

    # ------------------------------------------------------------------
    # Per-category breakdown
    # ------------------------------------------------------------------
    all_cats = sorted({c for t in summary["per_model"].values() for c in t.get("per_category", {})})
    if all_cats:
        cat_w = max(len(c) for c in all_cats) + 2
        print()
        print(_c("=" * 78, "1"))
        print(_c(" Per-category score (max 10)", "1"))
        print(_c("=" * 78, "1"))
        print(f"  {'model':<{width}}  " + "  ".join(f"{c:>{cat_w}}" for c in all_cats))
        for name, t in rows:
            cells = []
            for c in all_cats:
                pc = t["per_category"].get(c)
                if pc:
                    s = pc["score"]
                    raw = f"{s:>4.1f}/{pc['total']}"
                    if pc["accuracy"] >= 0.75:    raw = _c(raw, "92")
                    elif pc["accuracy"] >= 0.5:   raw = _c(raw, "96")
                    elif pc["accuracy"] >= 0.25:  raw = _c(raw, "93")
                    else:                         raw = _c(raw, "91")
                    cells.append(raw)
                else:
                    cells.append("-")
            print(f"  {name:<{width}}  " + "  ".join(_rpad(cell, cat_w) for cell in cells))

    # ------------------------------------------------------------------
    # Cost summary
    # ------------------------------------------------------------------
    print()
    print(_c("=" * 78, "1"))
    print(_c(" Cost ($ spent this run)", "1"))
    print(_c("=" * 78, "1"))
    grand = summary.get("total_cost_usd", 0.0)
    model_total = sum(t["model_cost_usd"] for t in summary["per_model"].values())
    judge_total = sum(t["judge_cost_usd"] for t in summary["per_model"].values())
    print(f"  total: {_c(f'${grand:.2f}', '1')}   (models: ${model_total:.2f}   judges: ${judge_total:.2f})")
    print()
    cost_rows = sorted(summary["per_model"].items(), key=lambda kv: -kv[1]["total_cost_usd"])
    for name, t in cost_rows:
        pct = (t["total_cost_usd"] / grand * 100) if grand else 0
        bar = _bar(t["total_cost_usd"], cost_rows[0][1]["total_cost_usd"] or 1, width=20)
        print(f"  {name:<{width}}  ${t['total_cost_usd']:>6.3f}  {bar}  {pct:>4.1f}%")

    # ------------------------------------------------------------------
    # Token usage
    # ------------------------------------------------------------------
    print()
    print(_c("=" * 78, "1"))
    print(_c(" Token usage per model (in / out, with reasoning subset)", "1"))
    print(_c("=" * 78, "1"))
    print(f"  {'model':<{width}}   {'input':>8}   {'output':>8}   {'reasoning':>10}   {'avg-lat':>7}")
    for name, t in rows:
        avg_lat = t.get("avg_model_latency_s", 0.0) + t.get("avg_judge_latency_s", 0.0)
        print(f"  {name:<{width}}   {t['model_input_tokens']:>8}   "
              f"{t['model_output_tokens']:>8}   {t['model_reasoning_tokens']:>10}   "
              f"{avg_lat:>6.1f}s")

    # ------------------------------------------------------------------
    # Per-model issues
    # ------------------------------------------------------------------
    print()
    print(_c("=" * 78, "1"))
    print(_c(" Issues per model", "1"))
    print(_c("=" * 78, "1"))
    for name, t in rows:
        missed = t.get("missed_ids", [])
        errs   = t.get("error_ids", [])
        trunc  = t.get("truncated_count", 0)
        no_fa  = t.get("missing_final_answer_line", 0)
        parts = []
        if missed: parts.append(f"missed: {', '.join(missed)}")
        if errs:   parts.append(_c(f"error: {', '.join(errs)}", "93"))
        if trunc:  parts.append(_c(f"truncated×{trunc}", "95"))
        if no_fa:  parts.append(_c(f"no-Final-Answer×{no_fa}", "93"))
        detail = "  |  ".join(parts) if parts else _c("(all max)", "92")
        print(f"  {name:<{width}}  {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
