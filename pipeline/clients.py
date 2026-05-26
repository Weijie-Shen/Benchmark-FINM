"""OpenRouter client + the two LLM judges.

Three public async entry points:
  - call_model(cfg, prompt)                 -> str
      Single-shot call to one of the models under test.
  - judge_answer(question, expected, raw)   -> (correct, extracted, judge_text)
      Binary judge for number / string / choice / unspecified answer types.
  - judge_rubric_score(question, rubric, raw) -> (raw_total, judge_text, breakdown)
      Rubric judge for `answer_type == "open"` questions.

All calls go through one shared `AsyncOpenAI` client pointed at OpenRouter.
The SDK handles 5xx / 429 / connection retries with exponential backoff.
"""
from __future__ import annotations

import json
import os
import re

from .models import ModelConfig
from .prompts import (
    JUDGE_SYSTEM_PROMPT,
    MODEL_SYSTEM_PROMPT,
    RUBRIC_JUDGE_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Per-HTTP-call timeouts (seconds). The SDK raises openai.APITimeoutError if
# a single call exceeds this — the runner catches it as a cell-level error.
MODEL_CALL_TIMEOUT_S = 120.0
JUDGE_CALL_TIMEOUT_S = 60.0

# When sending the model's reply to the judge, keep this many chars from each
# end. Reasoning models sometimes state the final answer near the top and
# then ramble — keeping both ends prevents false negatives.
JUDGE_EXCERPT_HEAD = 4000
JUDGE_EXCERPT_TAIL = 4000

# Single judge for both binary and rubric paths. DeepSeek-v4-pro is ~10x
# cheaper than Sonnet for comparable instruction-following on this task.
JUDGE_MODEL = "deepseek/deepseek-v4-pro"


class RubricJudgeParseError(RuntimeError):
    """Raised when the rubric judge's reply can't be parsed: missing
    ```json block, malformed JSON, or empty `scores` list. The cell is
    recorded as `error` (re-runnable) — not as the model scoring 0."""


class BinaryJudgeParseError(RuntimeError):
    """Raised when the binary judge returns an empty / unparseable reply.
    Like RubricJudgeParseError, the cell is recorded as `error` so it can
    be re-run — never as the model scoring 0 due to a silent judge failure."""


class EmptyChoicesError(RuntimeError):
    """Raised when an OpenRouter response comes back with `choices=None`
    or empty (sometimes happens on upstream content-filter rejections that
    the SDK doesn't surface as an exception). Cell is recorded as `error`."""


# ---------------------------------------------------------------------------
# Client (lazy singleton)
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    from openai import AsyncOpenAI   # lazy import
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set. Add it to .env.")

    _client = AsyncOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=MODEL_CALL_TIMEOUT_S,
        max_retries=3,     # SDK retries 5xx / 429 / connection errors w/ backoff
        default_headers={
            "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", ""),
            "X-Title":      os.environ.get("OPENROUTER_TITLE", "Quant Interview Benchmark"),
        },
    )
    return _client


def has_api_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _excerpt_for_judge(text: str) -> str:
    """Truncate `text` by keeping head + tail, dropping any middle that
    exceeds the budget. Returns text unchanged if it already fits."""
    head, tail = JUDGE_EXCERPT_HEAD, JUDGE_EXCERPT_TAIL
    if len(text) <= head + tail + 64:
        return text
    dropped = len(text) - head - tail
    return (
        f"{text[:head]}\n\n"
        f"[... {dropped} chars truncated from middle ...]\n\n"
        f"{text[-tail:]}"
    )


def _parse_rubric_judge_output(text: str, rubric: dict) -> tuple[float, list[dict]]:
    """Parse the judge's JSON reply, clamp scores, and return (raw_total, breakdown).

    The judge call uses `response_format={"type": "json_object"}`, so we expect
    pure JSON. We still try a fenced ```json``` fallback in case some provider
    silently drops the param. Clamping is essential: LLM judges sometimes
    award more than a criterion's max — we trust the rubric, not the judge.
    """
    # Primary path: pure JSON object (response_format enforced).
    # Fallback: extract from a ```json``` fenced block if present.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
        if not m:
            raise RubricJudgeParseError(
                f"no parseable JSON in judge reply (got {len(text)} chars)")
        try:
            parsed = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise RubricJudgeParseError(f"JSON decode failed: {e}")

    breakdown = parsed.get("scores")
    if not isinstance(breakdown, list) or not breakdown:
        raise RubricJudgeParseError(f"missing or empty 'scores': {parsed!r}")

    # Clamp each criterion score to [0, max_points].
    max_by_id = {cr["id"]: float(cr["points"])
                 for cat in rubric["categories"] for cr in cat["criteria"]}
    for item in breakdown:
        cid = item.get("id")
        if cid in max_by_id:
            try:
                item["score"] = max(0.0, min(float(item.get("score", 0)), max_by_id[cid]))
            except (TypeError, ValueError):
                item["score"] = 0.0

    # Recompute total from the clamped scores; clamp once more to the global cap.
    raw_total = sum(item["score"] for item in breakdown
                    if isinstance(item.get("score"), (int, float)))
    return min(raw_total, float(rubric["total_points"])), breakdown


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
async def call_model(cfg: ModelConfig, prompt: str) -> tuple[str, str]:
    """Ask the model under test to answer `prompt`. Single HTTP call, no tools.

    Returns `(content, finish_reason)`. `finish_reason == "length"` means the
    model hit the `max_tokens` cap before finishing — the runner counts that
    as a wrong answer (the model failed to produce a final answer in budget).

    The SDK-level timeout (MODEL_CALL_TIMEOUT_S) raises openai.APITimeoutError
    on overrun; the runner catches that as a cell-level error.
    """
    client = _get_client()
    resp = await client.chat.completions.create(
        model=cfg.model_id,
        temperature=0,
        top_p=1.0,
        max_tokens=8192,
        messages=[
            {"role": "system", "content": MODEL_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
    )
    # Some providers occasionally return a 200 with `choices=None` (e.g. upstream
    # content filter rejection that the SDK doesn't surface as an exception).
    # Treat as a re-runnable error rather than silently scoring 0.
    if not resp.choices:
        raise EmptyChoicesError(f"empty choices in response from {cfg.model_id}")
    choice = resp.choices[0]
    return (choice.message.content or "", choice.finish_reason or "stop")


async def judge_answer(question: str, expected: str,
                       raw_response: str) -> tuple[bool, str, str]:
    """Binary judge. Returns (is_correct, extracted_answer, full_judge_text)."""
    if not raw_response.strip():
        return False, "N/A", "model returned empty response"

    client = _get_client().with_options(timeout=JUDGE_CALL_TIMEOUT_S)
    resp = await client.chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0,
        top_p=1.0,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user",   "content": (
                f"Question:\n{question}\n\n"
                f"Expected answer: {expected}\n\n"
                f"Model response (head + tail; middle may be truncated):\n"
                f"{_excerpt_for_judge(raw_response)}"
            )},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    if not lines:
        raise BinaryJudgeParseError(
            "binary judge returned empty reply (would have silently scored 0)")

    # Judge is asked for exactly 3 lines (extracted / reason / YES|NO).
    # Be tolerant if it deviates: extracted = first line, verdict = last line.
    extracted = lines[0]
    verdict = lines[-1].upper()
    is_correct = verdict.startswith("YES")
    return is_correct, extracted, text


async def judge_rubric_score(question: str, rubric: dict,
                             raw_response: str) -> tuple[float, str, list]:
    """Rubric judge. Returns (raw_total, full_judge_text, per_criterion_breakdown).
    Raises RubricJudgeParseError if the judge's reply can't be parsed.
    """
    if not raw_response.strip():
        return 0.0, "model returned empty response", []

    client = _get_client().with_options(timeout=JUDGE_CALL_TIMEOUT_S)
    resp = await client.chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0,
        top_p=1.0,
        max_tokens=1500,
        response_format={"type": "json_object"},   # forces pure JSON reply
        messages=[
            {"role": "system", "content": RUBRIC_JUDGE_SYSTEM_PROMPT},
            {"role": "user",   "content": (
                f"Question:\n{question}\n\n"
                f"Rubric:\n{json.dumps(rubric, indent=2, ensure_ascii=False)}\n\n"
                f"Model response (head + tail; middle may be truncated):\n"
                f"{_excerpt_for_judge(raw_response)}"
            )},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    raw_total, breakdown = _parse_rubric_judge_output(text, rubric)
    return raw_total, text, breakdown
