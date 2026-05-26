"""Orchestrate the benchmark: every (model, question) pair, in parallel.

Public entry: `run_benchmark(questions_path, out_dir, concurrency)`.

This file owns:
  - the `Result` schema (one row per (model, question))
  - the per-pair evaluation logic (call model, dispatch to a judge, record)
  - the top-level fan-out / gather

Question loading + validation lives in `dataset.py`.
Output file writing lives in `output.py`.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import openai

from .clients import (
    BinaryJudgeParseError, EmptyChoicesError, RubricJudgeParseError,
    call_model, has_api_key, judge_answer, judge_rubric_score,
)
from .dataset import load_questions
from .models import MODELS, ModelConfig
from .output import write_outputs


# Exceptions we record as cell-level errors so the rest of the run continues.
# Anything else propagates — those are pipeline bugs that should surface loudly.
# (openai.APITimeoutError is a subclass of openai.APIError, so SDK timeouts are covered.)
_TRANSIENT_EXC = (
    BinaryJudgeParseError, EmptyChoicesError, RubricJudgeParseError, openai.APIError,
)


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------
@dataclass
class Result:
    model: str
    question_id: str
    score: float                       # 0.0-1.0 contribution to the total
    correct: Optional[bool]            # binary verdict; None for rubric
    extracted_answer: Optional[str]    # binary: judge line-1; rubric: "rubric:N/M"
    expected_answer: object            # str for binary; dict (rubric) for open
    raw_response: str
    judge_reasoning: Optional[str]
    latency_s: float
    sampling_controlled: bool          # False => model ignored our temp/top_p
    rubric_score: Optional[float] = None     # raw 0..total_points (open only)
    rubric_breakdown: Optional[list] = None  # per-criterion scores (open only)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Per-pair evaluation
# ---------------------------------------------------------------------------
async def _eval_one(cfg: ModelConfig, q: dict, sem: asyncio.Semaphore,
                    progress: dict) -> Result:
    """Evaluate one (model, question) pair end-to-end.

    Transient API failures and judge-parse failures are captured into
    `Result.error`; other exceptions propagate as pipeline bugs.
    """
    async with sem:
        start = time.time()
        try:
            raw, finish_reason = await call_model(cfg, q["question"])
            if finish_reason == "length":
                # Model ran out of budget before producing a Final Answer line.
                # Count as wrong (no judge call) — not a re-runnable error.
                result = _truncated_result(cfg, q, start, raw)
            elif q.get("answer_type") == "open":
                result = await _evaluate_rubric(cfg, q, raw, start)
            else:
                result = await _evaluate_binary(cfg, q, raw, start)
        except _TRANSIENT_EXC as e:
            result = _error_result(cfg, q, start, exc=e)
        await _persist_progress(result, progress)
        return result


async def _evaluate_binary(cfg, q, raw, start) -> Result:
    expected = str(q["answer"])
    is_correct, extracted, reasoning = await judge_answer(
        q["question"], expected, raw)
    return Result(
        model=cfg.name, question_id=q["id"],
        score=1.0 if is_correct else 0.0, correct=is_correct,
        extracted_answer=extracted, expected_answer=expected,
        raw_response=raw, judge_reasoning=reasoning,
        latency_s=time.time() - start,
        sampling_controlled=cfg.supports_temperature,
    )


async def _evaluate_rubric(cfg, q, raw, start) -> Result:
    rubric = q["answer"]
    total_points = float(rubric["total_points"])
    raw_total, reasoning, breakdown = await judge_rubric_score(
        q["question"], rubric, raw)
    score = raw_total / total_points if total_points else 0.0
    return Result(
        model=cfg.name, question_id=q["id"],
        score=score, correct=None,
        extracted_answer=f"rubric:{raw_total:g}/{total_points:g}",
        expected_answer=rubric,
        raw_response=raw, judge_reasoning=reasoning,
        latency_s=time.time() - start,
        sampling_controlled=cfg.supports_temperature,
        rubric_score=raw_total, rubric_breakdown=breakdown,
    )


def _truncated_result(cfg, q, start, raw) -> Result:
    """Model hit max_tokens before producing a Final Answer. Score 0, no judge."""
    return Result(
        model=cfg.name, question_id=q["id"],
        score=0.0, correct=False,
        extracted_answer="[truncated]", expected_answer=q.get("answer"),
        raw_response=raw,
        judge_reasoning="model output truncated at max_tokens; counted as wrong",
        latency_s=time.time() - start,
        sampling_controlled=cfg.supports_temperature,
    )


def _error_result(cfg, q, start, exc) -> Result:
    return Result(
        model=cfg.name, question_id=q["id"],
        score=0.0, correct=False,
        extracted_answer=None, expected_answer=q.get("answer"),
        raw_response="", judge_reasoning=None,
        latency_s=time.time() - start,
        sampling_controlled=cfg.supports_temperature,
        error=f"{type(exc).__name__}: {exc}",
    )


async def _persist_progress(r: Result, progress: dict) -> None:
    """Append `r` to the run's JSONL, bump the counter, print a status line.
    The write + counter bump happen under a lock so the printed counter
    matches the on-disk JSONL line count exactly."""
    async with progress["lock"]:
        progress["done"] += 1
        n_done = progress["done"]
        try:
            with open(progress["jsonl_path"], "a") as f:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[runner] WARN: jsonl write failed: {e}", flush=True)

    # Status mark: E for error, the numeric rubric score for open questions,
    # ✓/✗ for binary.
    if r.error:
        mark = "E"
    elif r.rubric_score is not None:
        mark = f"{r.rubric_score:g}"
    else:
        mark = "✓" if r.correct else "✗"
    print(f"  [{n_done:>3}/{progress['total']}] {r.model:25s} {r.question_id:>4}  "
          f"{mark:<3} {r.latency_s:5.1f}s", flush=True)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------
async def run_benchmark(
    questions_path: Path,
    out_dir: Path,
    concurrency: int = 8,
) -> dict:
    if not has_api_key():
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to .env.")
    questions = load_questions(questions_path)
    if not questions:
        raise RuntimeError(f"No questions loaded from {questions_path}. "
                           "Add some to a JSON file before running.")
    models = list(MODELS)

    print(f"[runner] {len(models)} models x {len(questions)} questions "
          f"= {len(models) * len(questions)} calls")
    uncontrolled = [m.name for m in models if not m.supports_temperature]
    if uncontrolled:
        print(f"[runner] sampling NOT controlled for: {', '.join(uncontrolled)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"details_{timestamp}.jsonl"

    sem = asyncio.Semaphore(concurrency)
    progress = {
        "done": 0,
        "total": len(models) * len(questions),
        "lock": asyncio.Lock(),
        "jsonl_path": jsonl_path,
    }
    tasks = [_eval_one(m, q, sem, progress) for m in models for q in questions]
    results: list[Result] = await asyncio.gather(*tasks)

    return write_outputs(results, models, questions, out_dir, timestamp)
