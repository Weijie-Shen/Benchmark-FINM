#!/usr/bin/env python3
"""Resume an aborted benchmark from its partial `details_*.jsonl`.

Scans for partial JSONLs in the results dir (filtering out any from older
model lineups), figures out which (model, question) pairs are still
missing, runs ONLY those, then writes a fresh timestamped set of output
files merging old + new. Original partials are never touched.

Usage:
    python resume_run.py                          # auto-union fresh partials
    python resume_run.py --partial path.jsonl     # explicit (repeatable)
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
    Result, _eval_one, make_progress, row_to_result, run_rejudge_phase,
)


def _load_fresh_partials(paths: list[Path], current_models: set[str],
                         all_keys: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Read each JSONL; skip any file that has rows for models outside the
    current lineup (those are from older configs and would be stale).
    Returns {(model, qid): latest_row} across all fresh files."""
    completed: dict[tuple[str, str], dict] = {}
    stale: list[tuple[Path, str]] = []
    for p in paths:
        rows = []
        foreign = None
        for line in open(p):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)
            if foreign is None and row["model"] not in current_models:
                foreign = row["model"]
        if foreign:
            stale.append((p, foreign))
            continue
        for row in rows:
            key = (row["model"], row["question_id"])
            if key in all_keys:
                completed[key] = row

    print(f"[resume] fresh partials: {len(paths) - len(stale)} file(s), "
          f"{len(completed)} unique cells")
    for p in paths:
        if not any(p == sp for sp, _ in stale):
            print(f"   - {p.name}")
    if stale:
        print(f"[resume] SKIPPED {len(stale)} stale partial(s) "
              f"(rows from models not in current lineup):")
        for p, fm in stale:
            print(f"   - {p.name}  (e.g. model={fm!r})")
    return completed


async def main_async(partial_paths: list[Path], questions_path: Path,
                     out_dir: Path, concurrency: int, label: str | None) -> None:
    if not has_api_key():
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to .env.")
    questions = load_questions(questions_path)
    models = list(MODELS)
    all_keys = {(m.name, q["id"]) for m in models for q in questions}

    completed = _load_fresh_partials(
        partial_paths, current_models={m.name for m in models}, all_keys=all_keys)
    missing = sorted(all_keys - set(completed.keys()))
    print(f"[resume] missing: {len(missing)} cells")
    if not missing:
        print("[resume] nothing to run — rewriting outputs from union of partials")

    cfg_by_name = {m.name: m for m in models}
    q_by_id = {q["id"]: q for q in questions}

    suffix = label or time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"details_{suffix}.jsonl"
    progress = make_progress(jsonl_path, len(missing))
    sem = asyncio.Semaphore(concurrency)

    tasks = [_eval_one(cfg_by_name[m], q_by_id[q], sem, progress) for m, q in missing]
    new_results = await asyncio.gather(*tasks)

    merged: list[Result] = [row_to_result(r) for r in completed.values()]
    merged.extend(new_results)

    await run_rejudge_phase(merged, models, questions, jsonl_path, label="resume")
    write_outputs(merged, models, questions, out_dir, suffix)
    print(f"\n[resume] total: {len(merged)} cells, errors remaining: "
          f"{sum(1 for r in merged if r.error)}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--partial", action="append",
                   help="path to a partial details_*.jsonl; pass multiple times "
                        "to union them. Default: all details_*.jsonl in results/.")
    p.add_argument("--questions", default="data")
    p.add_argument("--out", default="results")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--label",
                   help="suffix for output files, e.g. 'run2_resumed'. "
                        "Default: timestamp.")
    args = p.parse_args()

    out_dir = Path(args.out)
    if args.partial:
        partials = [Path(p) for p in args.partial]
    else:
        partials = sorted(out_dir.glob("details_*.jsonl"))
        if not partials:
            print(f"error: no details_*.jsonl in {out_dir}")
            return 1

    asyncio.run(main_async(partials, Path(args.questions), out_dir,
                           args.concurrency, args.label))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
