"""Per-cell evaluation, Result schema, and shared run-orchestration helpers.

This file is imported by all three entry scripts (run_benchmark / resume_run /
rerun_errors). What lives here:
  - `Result` — the per-(model, question) row schema
  - `_eval_one` — call model, dispatch to a judge, build a Result
  - `_rejudge_cell` — re-call the judge reusing a preserved raw_response
  - `run_rejudge_phase` — post-gather sweep over a results list
  - `row_to_result` — JSON row → Result (used by resume / rerun)
  - `make_progress` — build the {done, total, spent, lock, jsonl_path} dict
  - `run_benchmark` — the standard full run

The live-stream / color rendering also lives here (`_persist_progress`) so
every entry script gets a consistent progress display for free.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .clients import (
    CallStats, call_model, has_api_key, judge_answer, judge_rubric_score,
)
from .dataset import load_questions
from .errors import TRANSIENT_EXC as _TRANSIENT_EXC
from .models import MODELS, ModelConfig
from .output import write_outputs


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------
@dataclass
class Result:
    """Per-(model, question) row. All observability fields after the base
    set are optional with sensible zero defaults — this makes the schema
    forward-compatible: rows written by older code (missing newer fields)
    still round-trip cleanly through `Result(**row_dict)`.
    """
    # Base identity / scoring (required — must be present in every row)
    model: str
    question_id: str
    score: float                       # 0.0-1.0 contribution to the total
    correct: Optional[bool]            # binary verdict; None for rubric
    extracted_answer: Optional[str]    # binary: judge line-1; rubric: "rubric:N/M"
    expected_answer: object            # str/list/dict — preserved raw
    raw_response: str
    judge_reasoning: Optional[str]
    sampling_controlled: bool          # False => model ignored our temp/top_p

    # Latency split — defaults so older rows still load
    model_latency_s: float = 0.0
    judge_latency_s: float = 0.0
    latency_s: float = 0.0             # = model + judge

    # Tokens (model)
    model_input_tokens: int = 0
    model_output_tokens: int = 0       # includes reasoning_tokens
    model_reasoning_tokens: int = 0    # 0 if provider didn't report it
    model_finish_reason: str = "stop"  # "stop" / "length" / "content_filter" / ...
    # Tokens (judge)
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_reasoning_tokens: int = 0
    # Cost
    model_cost_usd: float = 0.0
    judge_cost_usd: float = 0.0
    # Output-contract compliance
    has_final_answer_line: bool = False
    # Rubric (open answers only)
    rubric_score: Optional[float] = None
    rubric_breakdown: Optional[list] = None
    # Error (null if no error)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Per-pair evaluation
# ---------------------------------------------------------------------------
async def _eval_one(cfg: ModelConfig, q: dict, sem: asyncio.Semaphore,
                    progress: dict) -> Result:
    """Evaluate one (model, question) pair end-to-end.
    Splits the try block so a judge failure still preserves the model's real
    latency / tokens / cost (model actually answered — only the judge bailed)."""
    async with sem:
        try:
            raw, model_stats = await call_model(cfg, q["question"])
        except _TRANSIENT_EXC as e:
            # Model call itself failed — no model_stats / no raw_response.
            result = _error_result(cfg, q, exc=e, raw="", model_stats=CallStats())
        else:
            try:
                if model_stats.finish_reason == "length":
                    result = _truncated_result(cfg, q, raw, model_stats)
                elif q.get("answer_type") == "open":
                    result = await _evaluate_rubric(cfg, q, raw, model_stats)
                else:
                    result = await _evaluate_binary(cfg, q, raw, model_stats)
            except _TRANSIENT_EXC as e:
                # Judge failed — preserve raw_response + model_stats so the
                # post-gather rejudge phase can re-call the judge without
                # paying for the model again.
                result = _error_result(cfg, q, exc=e, raw=raw, model_stats=model_stats)
        await _persist_progress(result, progress)
        return result


async def _evaluate_binary(cfg, q, raw, model_stats: CallStats) -> Result:
    # Multi-select MCQ (answer_type=="choice") may give answer as a list,
    # e.g. ["A", "B", "D"]. Normalize to "A, B, D" for the judge prompt.
    expected_raw = q["answer"]
    if isinstance(expected_raw, list):
        expected = ", ".join(str(x) for x in expected_raw)
    else:
        expected = str(expected_raw)
    is_correct, extracted, reasoning, judge_stats = await judge_answer(
        q["question"], expected, raw)
    return _make_result(
        cfg, q, raw, model_stats, judge_stats,
        score=1.0 if is_correct else 0.0,
        correct=is_correct,
        extracted_answer=extracted,
        expected_answer=expected_raw,
        judge_reasoning=reasoning,
    )


async def _evaluate_rubric(cfg, q, raw, model_stats: CallStats) -> Result:
    rubric = q["answer"]
    total_points = float(rubric["total_points"])
    raw_total, reasoning, breakdown, judge_stats = await judge_rubric_score(
        q["question"], rubric, raw)
    score = raw_total / total_points if total_points else 0.0
    return _make_result(
        cfg, q, raw, model_stats, judge_stats,
        score=score, correct=None,
        extracted_answer=f"rubric:{raw_total:g}/{total_points:g}",
        expected_answer=rubric,
        judge_reasoning=reasoning,
        rubric_score=raw_total, rubric_breakdown=breakdown,
    )


def _truncated_result(cfg, q, raw, model_stats: CallStats) -> Result:
    """Model hit max_tokens before producing a Final Answer. Score 0, no judge."""
    return _make_result(
        cfg, q, raw, model_stats, CallStats(),
        score=0.0, correct=False,
        extracted_answer="[truncated]",
        expected_answer=q.get("answer"),
        judge_reasoning="model output truncated at max_tokens; counted as wrong",
    )


def _error_result(cfg, q, exc, raw: str = "", model_stats: "CallStats | None" = None) -> Result:
    """Transient error — record so the cell is re-runnable, not silently scored 0.
    If the model call succeeded but the judge failed, `raw` and `model_stats`
    carry the real model output / cost / tokens so a rejudge can recover the
    cell without re-paying for the model."""
    return _make_result(
        cfg, q, raw=raw,
        model_stats=model_stats if model_stats is not None else CallStats(),
        judge_stats=CallStats(),
        score=0.0, correct=False,
        extracted_answer=None,
        expected_answer=q.get("answer"),
        judge_reasoning=None,
        error=f"{type(exc).__name__}: {exc}",
    )


async def _rejudge_cell(cfg: ModelConfig, q: dict, prev: Result) -> Result:
    """Re-call the judge for a cell whose first judge attempt failed but the
    model had already answered. Reuses prev.raw_response — does NOT call
    the model again. Returns a new Result with the new judge outcome, or a
    fresh error if the rejudge fails too."""
    model_stats = CallStats(
        latency_s=prev.model_latency_s,
        input_tokens=prev.model_input_tokens,
        output_tokens=prev.model_output_tokens,
        reasoning_tokens=prev.model_reasoning_tokens,
        cost_usd=prev.model_cost_usd,
        finish_reason=prev.model_finish_reason or "stop",
    )
    try:
        if q.get("answer_type") == "open":
            return await _evaluate_rubric(cfg, q, prev.raw_response, model_stats)
        return await _evaluate_binary(cfg, q, prev.raw_response, model_stats)
    except _TRANSIENT_EXC as e:
        return _error_result(cfg, q, exc=e, raw=prev.raw_response, model_stats=model_stats)


def _make_result(
    cfg, q, raw, model_stats: CallStats, judge_stats: CallStats,
    *, score, correct, extracted_answer, expected_answer, judge_reasoning,
    rubric_score=None, rubric_breakdown=None, error=None,
) -> Result:
    """Single constructor — keeps the per-cell field-stitching in one place."""
    return Result(
        model=cfg.name, question_id=q["id"],
        score=score, correct=correct,
        extracted_answer=extracted_answer, expected_answer=expected_answer,
        raw_response=raw, judge_reasoning=judge_reasoning,
        sampling_controlled=cfg.supports_temperature,
        model_latency_s=model_stats.latency_s,
        judge_latency_s=judge_stats.latency_s,
        latency_s=model_stats.latency_s + judge_stats.latency_s,
        model_input_tokens=model_stats.input_tokens,
        model_output_tokens=model_stats.output_tokens,
        model_reasoning_tokens=model_stats.reasoning_tokens,
        model_finish_reason=model_stats.finish_reason,
        judge_input_tokens=judge_stats.input_tokens,
        judge_output_tokens=judge_stats.output_tokens,
        judge_reasoning_tokens=judge_stats.reasoning_tokens,
        model_cost_usd=model_stats.cost_usd,
        judge_cost_usd=judge_stats.cost_usd,
        has_final_answer_line=_has_final_answer(raw),
        rubric_score=rubric_score,
        rubric_breakdown=rubric_breakdown,
        error=error,
    )


def _has_final_answer(raw: str) -> bool:
    """True if the model ended with a 'Final Answer:' line, per the output
    contract in MODEL_SYSTEM_PROMPT. Cheap audit: just substring + tail check."""
    if not raw:
        return False
    tail = raw[-500:]
    return "Final Answer:" in tail or "final answer:" in tail.lower()


# ---------------------------------------------------------------------------
# Live progress stream (color-aware)
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    """ANSI-wrap `text` only when stdout is a TTY."""
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def _format_mark(r: Result) -> str:
    """Per-cell status mark, color-coded for live stream.
       E = error (yellow), ✗ = wrong (red), ✓ = correct (green),
       numeric = rubric raw score (cyan), [trunc] = model truncation (magenta)."""
    if r.error:
        return _c("E", "93")            # yellow
    if r.extracted_answer == "[truncated]":
        return _c("T", "95")            # magenta
    if r.rubric_score is not None:
        return _c(f"{r.rubric_score:g}", "96")   # cyan
    return _c("✓", "92") if r.correct else _c("✗", "91")  # green / red


async def _persist_progress(r: Result, progress: dict) -> None:
    """Append `r` to the run's JSONL and emit a live status line.
    The JSONL write + counter bump happen under a lock so the printed
    counter matches the on-disk line count exactly."""
    async with progress["lock"]:
        progress["done"] += 1
        n_done = progress["done"]
        progress["spent"] += r.model_cost_usd + r.judge_cost_usd
        try:
            with open(progress["jsonl_path"], "a") as f:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[runner] WARN: jsonl write failed: {e}", flush=True)
        running_spent = progress["spent"]

    mark = _format_mark(r)
    # 3 chars wide for mark (with color codes that don't count toward visible width)
    pad = 3 if not _USE_COLOR else 3 + (len(mark) - 1)
    cell_cost = r.model_cost_usd + r.judge_cost_usd
    print(
        f"  [{n_done:>3}/{progress['total']}] "
        f"{r.model:21s} {r.question_id:>4}  "
        f"{mark:<{pad}}  "
        f"M:{r.model_latency_s:5.1f}s J:{r.judge_latency_s:4.1f}s  "
        f"{_c(f'${cell_cost:.4f}', '2')}  "
        f"{_c(f'(run: ${running_spent:.2f})', '2')}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Shared orchestration helpers (used by run_benchmark + resume_run + rerun_errors)
# ---------------------------------------------------------------------------
def make_progress(jsonl_path: Path, total: int) -> dict:
    """Build the dict that `_persist_progress` mutates as cells complete."""
    return {
        "done": 0, "total": total, "spent": 0.0,
        "lock": asyncio.Lock(), "jsonl_path": jsonl_path,
    }


def row_to_result(row: dict) -> Result:
    """Reconstruct a Result from a JSONL/JSON row, dropping unknown fields.
    Defaults on the Result schema fill in any missing newer fields."""
    fields = {f.name for f in Result.__dataclass_fields__.values()}
    return Result(**{k: v for k, v in row.items() if k in fields})


async def run_rejudge_phase(results: list[Result], models, questions,
                            jsonl_path: Path, label: str = "runner") -> int:
    """Re-call the judge for any cell where the judge failed but `raw_response`
    was preserved. Mutates `results` in place. Appends each new row to the
    JSONL with a `# rejudge` marker. Returns the count of recovered cells."""
    cfg_by_name = {m.name: m for m in models}
    q_by_id = {q["id"]: q for q in questions}
    targets = [
        (i, cfg_by_name[r.model], q_by_id[r.question_id], r)
        for i, r in enumerate(results)
        if r.error and r.raw_response and "JudgeParseError" in r.error
           and r.model in cfg_by_name and r.question_id in q_by_id
    ]
    if not targets:
        return 0

    print(f"\n[{label}] re-judging {len(targets)} judge-parse errors "
          f"(reusing model output, no model re-calls)...")
    new = await asyncio.gather(*[_rejudge_cell(c, q, p) for _, c, q, p in targets])
    recovered = 0
    for (i, _, _, _), r in zip(targets, new):
        if not r.error:
            recovered += 1
        results[i] = r
        try:
            with open(jsonl_path, "a") as f:
                f.write("# rejudge\n")
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[{label}] WARN: rejudge jsonl append failed: {e}", flush=True)
    print(f"[{label}] re-judge recovered {recovered}/{len(targets)} cells")
    return recovered


# ---------------------------------------------------------------------------
# Top-level orchestration — standard full run
# ---------------------------------------------------------------------------
async def run_benchmark(questions_path: Path, out_dir: Path,
                        concurrency: int = 8, label: str | None = None) -> dict:
    """`label` becomes the filename suffix for the output files. Defaults to
    a timestamp `YYYYMMDD-HHMMSS`. Pass e.g. label='run1' to write
    `details_run1.{json,jsonl}`, `summary_run1.json`, `scores_run1.csv`."""
    if not has_api_key():
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to .env.")
    questions = load_questions(questions_path)
    if not questions:
        raise RuntimeError(f"No questions loaded from {questions_path}. "
                           "Add some to a JSON file before running.")
    models = list(MODELS)
    total = len(models) * len(questions)
    print(f"[runner] {len(models)} models x {len(questions)} questions = {total} calls")
    uncontrolled = [m.name for m in models if not m.supports_temperature]
    if uncontrolled:
        print(f"[runner] sampling NOT controlled for: {', '.join(uncontrolled)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = label or time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"details_{suffix}.jsonl"
    progress = make_progress(jsonl_path, total)
    sem = asyncio.Semaphore(concurrency)

    tasks = [_eval_one(m, q, sem, progress) for m in models for q in questions]
    results: list[Result] = await asyncio.gather(*tasks)

    await run_rejudge_phase(results, models, questions, jsonl_path, label="runner")
    return write_outputs(results, models, questions, out_dir, suffix)
