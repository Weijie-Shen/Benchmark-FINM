#!/usr/bin/env python3
"""Entry point for the quant interview benchmark.

Usage:
    python run_benchmark.py
    python run_benchmark.py --questions other.json --out results/run1 --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Load .env early so has_api_key() sees OPENROUTER_API_KEY.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline.runner import run_benchmark


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--questions", default="data/questions.json",
                   help="path to questions JSON (default: data/questions.json)")
    p.add_argument("--out", default="results",
                   help="output directory (default: results/)")
    p.add_argument("--concurrency", type=int, default=8,
                   help="max in-flight API calls (default: 8)")
    args = p.parse_args()

    questions_path = Path(args.questions)
    if not questions_path.exists():
        print(f"error: {questions_path} not found", file=sys.stderr)
        return 1

    summary = asyncio.run(run_benchmark(
        questions_path=questions_path,
        out_dir=Path(args.out),
        concurrency=args.concurrency,
    ))

    # Pretty leaderboard.
    print("\n=== Leaderboard ===")
    rows = sorted(summary["per_model"].items(), key=lambda kv: -kv[1]["score"])
    width = max(len(name) for name, _ in rows)
    print(f"{'model':<{width}}  score  accuracy  errors  sampling")
    for name, t in rows:
        sampling = "ctrl" if t["sampling_controlled"] else "UNCTRL"
        print(f"{name:<{width}}  {t['score']:>5}  {t['accuracy']:>7.1%}  "
              f"{t['errors']:>6}  {sampling}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
