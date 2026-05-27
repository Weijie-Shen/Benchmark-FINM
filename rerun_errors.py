#!/usr/bin/env python3
"""Re-run cells flagged with `error` in a previous run's details JSON.

Two recovery modes (auto-selected per cell):
  1. Cell has `raw_response` AND error is a JudgeParseError → just re-call
     the judge (cheap, no model cost).
  2. Otherwise (empty raw_response, or non-judge error) → re-run the full
     (model, question) pair.

The merged result is written under a NEW timestamp; the source file is
untouched.

Usage:
    python rerun_errors.py                       # newest details_*.json
    python rerun_errors.py --details path.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline.clients import has_api_key
from pipeline.dataset import load_questions
from pipeline.models import MODELS
from pipeline.output import write_outputs
from pipeline.runner import (
    Result, _eval_one, _persist_progress, _rejudge_cell,
    make_progress, row_to_result, run_rejudge_phase,
)


async def _rejudge_with_progress(cfg, q, prev: Result, sem, progress) -> Result:
    """Run `_rejudge_cell` through the standard progress stream + JSONL append,
    so a rejudge-only cell looks identical to a normal cell in the live output."""
    async with sem:
        result = await _rejudge_cell(cfg, q, prev)
    await _persist_progress(result, progress)
    return result


async def main_async(details_path: Path, questions_path: Path, out_dir: Path,
                     concurrency: int) -> None:
    if not has_api_key():
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to .env.")
    rows = json.load(open(details_path))
    print(f"[rerun] source: {details_path.name}  ({len(rows)} cells total)")

    err_rows = [r for r in rows if r.get("error")]
    print(f"[rerun] {len(err_rows)} error cells to fix")
    if not err_rows:
        return

    questions = load_questions(questions_path)
    models = list(MODELS)
    cfg_by_name = {m.name: m for m in models}
    q_by_id = {q["id"]: q for q in questions}

    rejudge_only, full_rerun = [], []
    for row in err_rows:
        cfg = cfg_by_name.get(row["model"])
        q = q_by_id.get(row["question_id"])
        if cfg is None or q is None:
            print(f"[rerun] WARN: skip {row['model']}/{row['question_id']} "
                  f"(not in current lineup)")
            continue
        if row.get("raw_response") and "JudgeParseError" in (row.get("error") or ""):
            rejudge_only.append((row, cfg, q))
        else:
            full_rerun.append((row, cfg, q))

    print(f"[rerun]   rejudge-only (cheap): {len(rejudge_only)}")
    print(f"[rerun]   full re-run (model + judge): {len(full_rerun)}")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"details_{timestamp}.jsonl"
    progress = make_progress(jsonl_path, len(rejudge_only) + len(full_rerun))
    sem = asyncio.Semaphore(concurrency)

    tasks = [
        _rejudge_with_progress(cfg, q, row_to_result(row), sem, progress)
        for row, cfg, q in rejudge_only
    ] + [
        _eval_one(cfg, q, sem, progress) for _, cfg, q in full_rerun
    ]
    new_results = await asyncio.gather(*tasks)

    # Merge: replace error rows with fresh results, keep everything else.
    by_key = {(r.model, r.question_id): r for r in new_results}
    merged = [by_key.get((row["model"], row["question_id"]), row_to_result(row))
              for row in rows]

    await run_rejudge_phase(merged, models, questions, jsonl_path, label="rerun")
    write_outputs(merged, models, questions, out_dir, timestamp)
    n_err = sum(1 for r in merged if r.error)
    print(f"\n[rerun] errors before: {len(err_rows)}  after: {n_err}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--details",
                   help="path to details_<ts>.json (default: newest in results/)")
    p.add_argument("--questions", default="data")
    p.add_argument("--out", default="results")
    p.add_argument("--concurrency", type=int, default=8)
    args = p.parse_args()

    out_dir = Path(args.out)
    if args.details:
        details_path = Path(args.details)
    else:
        candidates = sorted(out_dir.glob("details_*.json"))
        if not candidates:
            print(f"error: no details_*.json in {out_dir}")
            return 1
        details_path = candidates[-1]

    asyncio.run(main_async(details_path, Path(args.questions), out_dir, args.concurrency))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
