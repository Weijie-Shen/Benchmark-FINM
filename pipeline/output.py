"""Write the three benchmark output files for one run.

Public entry: `write_outputs(results, models, questions, out_dir, timestamp)`.
Writes:
  - details_<ts>.json   per-(model, question) rows (full info)
  - summary_<ts>.json   per-model totals + per-category breakdown
  - scores_<ts>.csv     model × question matrix, sorted by total score

This module doesn't import `Result` — it duck-types on attribute access.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path


def write_outputs(results, models, questions, out_dir: Path, timestamp: str) -> dict:
    details_path = out_dir / f"details_{timestamp}.json"
    summary_path = out_dir / f"summary_{timestamp}.json"
    scores_path  = out_dir / f"scores_{timestamp}.csv"

    with open(details_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)

    summary = _build_summary(results, models, questions, timestamp, details_path.name)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    _write_scores_csv(results, models, questions, summary, scores_path)

    print(f"[runner] wrote {details_path.name}, {summary_path.name}, {scores_path.name}")
    return summary


def _build_summary(results, models, questions, timestamp: str,
                   details_filename: str) -> dict:
    """Aggregate per-Result scores into a per-model summary with per-category
    breakdown. Output mirrors the on-disk `summary_<ts>.json` schema."""
    qid_topic = {q["id"]: q.get("topic", "uncategorized") for q in questions}
    qid_order = {q["id"]: i for i, q in enumerate(questions)}

    totals: dict = {m.name: {
        "score_total": 0.0, "total": 0, "errors": 0,
        "missed_ids": [], "error_ids": [],
        "per_category": defaultdict(lambda: {"score": 0.0, "total": 0}),
        "sampling_controlled": m.supports_temperature,
    } for m in models}

    # Accumulate every result into its model + per-category buckets.
    for r in sorted(results, key=lambda r: qid_order.get(r.question_id, 0)):
        t = totals[r.model]
        t["total"] += 1
        t["score_total"] += r.score
        t["per_category"][qid_topic[r.question_id]]["score"] += r.score
        t["per_category"][qid_topic[r.question_id]]["total"] += 1
        if r.error:
            t["errors"] += 1
            t["error_ids"].append(r.question_id)
        elif r.score < 1.0:
            t["missed_ids"].append(r.question_id)

    # Finalize: derived accuracy + plain-dict per_category.
    for t in totals.values():
        t["accuracy"] = t["score_total"] / t["total"] if t["total"] else 0.0
        t["per_category"] = {
            cat: {**c, "accuracy": c["score"] / c["total"] if c["total"] else 0.0}
            for cat, c in t["per_category"].items()
        }

    return {
        "timestamp": timestamp,
        "num_questions": len(questions),
        "num_models": len(models),
        "max_possible_score": float(len(questions)),
        "per_model": totals,
        "details_file": details_filename,
    }


def _write_scores_csv(results, models, questions, summary, path: Path) -> None:
    qids = [q["id"] for q in questions]
    by_model: dict = {}
    for r in results:
        by_model.setdefault(r.model, {})[r.question_id] = r

    sorted_models = sorted(
        models, key=lambda m: -summary["per_model"][m.name]["score_total"])

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model"] + qids + ["score", "accuracy", "sampling_controlled"])
        for m in sorted_models:
            t = summary["per_model"][m.name]
            row = [m.name]
            row += [_cell(by_model.get(m.name, {}).get(qid)) for qid in qids]
            row += [
                f"{t['score_total']:.2f}",
                f"{t['accuracy']:.3f}",
                "yes" if m.supports_temperature else "no",
            ]
            w.writerow(row)


def _cell(r):
    """Format one CSV cell. Binary -> 0 or 1 (int). Rubric -> 0.0-1.0 (str)."""
    if r is None:
        return ""
    if r.rubric_score is not None:
        return f"{r.score:.1f}"
    return int(r.score)
