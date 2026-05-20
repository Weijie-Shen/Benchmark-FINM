"""Run the benchmark: every (model, question) pair, in parallel, results saved."""
from __future__ import annotations

import asyncio
import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .clients import call_model, has_api_key, judge_extract
from .evaluator import grade
from .models import MODELS, ModelConfig


@dataclass
class Result:
    model: str
    question_id: str
    correct: bool
    extracted_answer: Optional[str]      # judge's verdict; "N/A" if no answer found
    expected_answer: str
    raw_response: str
    latency_s: float
    sampling_controlled: bool            # False = model ignored our temp/top_p
    error: Optional[str] = None


REQUIRED_QUESTION_FIELDS = ("id", "question", "answer", "answer_type")


def load_questions(path: Path) -> list[dict]:
    with open(path) as f:
        qs = json.load(f)
    _validate(qs)
    return qs


def _validate(qs) -> None:
    if not isinstance(qs, list) or not qs:
        raise ValueError("questions.json must be a non-empty JSON array")
    seen = set()
    for i, q in enumerate(qs):
        missing = [k for k in REQUIRED_QUESTION_FIELDS if k not in q]
        if missing:
            raise ValueError(f"question #{i}: missing fields {missing}")
        if q["id"] in seen:
            raise ValueError(f"duplicate question id: {q['id']}")
        seen.add(q["id"])
        if q["answer_type"] not in ("number", "string", "choice"):
            raise ValueError(f"question {q['id']}: answer_type must be number|string|choice")


async def _eval_one(cfg: ModelConfig, q: dict, sem: asyncio.Semaphore) -> Result:
    async with sem:
        start = time.time()
        try:
            raw = await call_model(cfg, q["question"])
            extracted = await judge_extract(raw)
            ok = grade(
                extracted,
                expected=str(q["answer"]),
                answer_type=q["answer_type"],
                tolerance=q.get("tolerance", 1e-4),
            )
            return Result(
                model=cfg.name, question_id=q["id"], correct=ok,
                extracted_answer=extracted, expected_answer=str(q["answer"]),
                raw_response=raw, latency_s=time.time() - start,
                sampling_controlled=cfg.supports_temperature,
            )
        except Exception as e:
            return Result(
                model=cfg.name, question_id=q["id"], correct=False,
                extracted_answer=None, expected_answer=str(q["answer"]),
                raw_response="", latency_s=time.time() - start,
                sampling_controlled=cfg.supports_temperature,
                error=f"{type(e).__name__}: {e}",
            )


async def run_benchmark(
    questions_path: Path,
    out_dir: Path,
    concurrency: int = 8,
) -> dict:
    if not has_api_key():
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to .env.")
    questions = load_questions(questions_path)
    models = list(MODELS)

    print(f"[runner] {len(models)} models x {len(questions)} questions "
          f"= {len(models) * len(questions)} calls")
    uncontrolled = [m.name for m in models if not m.supports_temperature]
    if uncontrolled:
        print(f"[runner] sampling NOT controlled for: {', '.join(uncontrolled)}")

    sem = asyncio.Semaphore(concurrency)
    tasks = [_eval_one(m, q, sem) for m in models for q in questions]
    results: list[Result] = await asyncio.gather(*tasks)

    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return _write_outputs(results, models, questions, out_dir, timestamp)


def _write_outputs(results, models, questions, out_dir, timestamp):
    # 1. Full detail JSON.
    details_path = out_dir / f"details_{timestamp}.json"
    with open(details_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)

    # 2. Per-model totals.
    totals = {m.name: {"correct": 0, "total": 0, "errors": 0,
                       "sampling_controlled": m.supports_temperature}
              for m in models}
    for r in results:
        t = totals[r.model]
        t["total"] += 1
        if r.correct:
            t["correct"] += 1
        if r.error:
            t["errors"] += 1
    for t in totals.values():
        t["score"] = t["correct"]
        t["accuracy"] = t["correct"] / t["total"] if t["total"] else 0.0

    summary = {
        "timestamp": timestamp,
        "num_questions": len(questions),
        "num_models": len(models),
        "per_model": totals,
        "details_file": details_path.name,
    }
    summary_path = out_dir / f"summary_{timestamp}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # 3. CSV: rows=models, cols=questions + totals + sampling flag.
    csv_path = out_dir / f"scores_{timestamp}.csv"
    qids = [q["id"] for q in questions]
    by_model = {}
    for r in results:
        by_model.setdefault(r.model, {})[r.question_id] = r
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model"] + qids + ["score", "accuracy", "sampling_controlled"])
        sorted_models = sorted(models, key=lambda m: -totals[m.name]["score"])
        for m in sorted_models:
            row = [m.name]
            for qid in qids:
                r = by_model.get(m.name, {}).get(qid)
                row.append(1 if r and r.correct else 0)
            row.append(totals[m.name]["score"])
            row.append(f"{totals[m.name]['accuracy']:.3f}")
            row.append("yes" if m.supports_temperature else "no")
            w.writerow(row)

    print(f"[runner] wrote {details_path.name}, {summary_path.name}, {csv_path.name}")
    return summary
