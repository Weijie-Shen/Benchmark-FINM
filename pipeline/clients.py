"""OpenRouter client + the two LLM judges.

Three public async entry points:
  - call_model(cfg, prompt)                 -> (content, CallStats)
      Single-shot call to one of the models under test.
  - judge_answer(question, expected, raw)   -> (correct, extracted, judge_text, CallStats)
      Binary judge for number / string / choice / unspecified answer types.
  - judge_rubric_score(question, rubric, raw) -> (raw_total, judge_text, breakdown, CallStats)
      Rubric judge for `answer_type == "open"` questions.

`CallStats` carries observability for one API call: latency, tokens (split by
input / output / reasoning), cost, and finish_reason. The runner stitches
model + judge stats into the per-cell Result.

All calls go through one shared `AsyncOpenAI` client pointed at OpenRouter.
The SDK handles 5xx / 429 / connection retries with exponential backoff.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

from .errors import BinaryJudgeParseError, EmptyChoicesError, RubricJudgeParseError
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

# Judge model. DeepSeek-v4-pro is a reasoning model — we give it generous
# completion budget so its internal reasoning tokens don't starve the
# visible output (the previous failure mode that produced empty replies).
JUDGE_MODEL = "deepseek/deepseek-v4-pro"
JUDGE_PRICE_IN = 0.435   # $ / 1M prompt tokens   (verified 2026-05-26)
JUDGE_PRICE_OUT = 0.870  # $ / 1M completion tokens

# Token caps for the two judge paths. Reasoning + visible output share this
# budget — if reasoning eats it all, content comes back empty. We give a
# generous cap because deepseek-v4-pro (the judge) is a reasoning model
# that easily burns 2000+ tokens internally on multi-fact or rubric cells.
BINARY_JUDGE_MAX_TOKENS = 4000
RUBRIC_JUDGE_MAX_TOKENS = 5000

# Model under test gets a more generous budget — reasoning models can chew
# through this. Truncation (finish_reason=="length") is treated as a wrong
# answer (cell scored 0), not as a re-runnable error.
MODEL_MAX_TOKENS = 8192


# ---------------------------------------------------------------------------
# Per-call observability
# ---------------------------------------------------------------------------
@dataclass
class CallStats:
    """One API call's observability. Shared shape for model and judge calls."""
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0       # full completion budget consumed (incl. reasoning)
    reasoning_tokens: int = 0    # subset of output_tokens, 0 if not reported
    cost_usd: float = 0.0
    finish_reason: str = "stop"


def _reasoning_tokens(usage) -> int:
    """Extract reasoning tokens from the SDK usage object, defensive against
    older / non-reasoning responses that don't include the details block."""
    details = getattr(usage, "completion_tokens_details", None)
    if details is None:
        return 0
    val = getattr(details, "reasoning_tokens", 0)
    return int(val or 0)


def _stats_from(resp, latency_s: float, price_in: float, price_out: float) -> CallStats:
    """Build a CallStats from a chat-completion response + wall-clock latency.

    Defensive: some OpenRouter providers occasionally return a response with
    `usage=None` (rare but real). When that happens we still return a usable
    CallStats — just with token counts / cost = 0. Latency + finish_reason
    are always recovered.
    """
    finish_reason = resp.choices[0].finish_reason or "stop"
    u = resp.usage
    if u is None:
        return CallStats(latency_s=latency_s, finish_reason=finish_reason)
    inp = int(u.prompt_tokens or 0)
    out = int(u.completion_tokens or 0)
    reasoning = _reasoning_tokens(u)
    cost = (inp * price_in + out * price_out) / 1_000_000
    return CallStats(
        latency_s=latency_s,
        input_tokens=inp,
        output_tokens=out,
        reasoning_tokens=reasoning,
        cost_usd=cost,
        finish_reason=finish_reason,
    )


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

    max_by_id = {cr["id"]: float(cr["points"])
                 for cat in rubric["categories"] for cr in cat["criteria"]}
    for item in breakdown:
        cid = item.get("id")
        if cid in max_by_id:
            try:
                item["score"] = max(0.0, min(float(item.get("score", 0)), max_by_id[cid]))
            except (TypeError, ValueError):
                item["score"] = 0.0

    raw_total = sum(item["score"] for item in breakdown
                    if isinstance(item.get("score"), (int, float)))
    return min(raw_total, float(rubric["total_points"])), breakdown


async def _call_judge(system: str, user: str, max_tokens: int,
                      response_format: dict | None = None) -> tuple[str, CallStats]:
    """Internal helper for the two judge paths. Returns (text, stats).
    Shares the HTTP/SDK setup that judge_answer and judge_rubric_score both need."""
    client = _get_client().with_options(timeout=JUDGE_CALL_TIMEOUT_S)
    kwargs = dict(model=JUDGE_MODEL, temperature=0, top_p=1.0,
                  max_tokens=max_tokens,
                  messages=[{"role": "system", "content": system},
                            {"role": "user",   "content": user}])
    if response_format is not None:
        kwargs["response_format"] = response_format

    t0 = time.time()
    resp = await client.chat.completions.create(**kwargs)
    latency = time.time() - t0
    stats = _stats_from(resp, latency, JUDGE_PRICE_IN, JUDGE_PRICE_OUT)
    text = (resp.choices[0].message.content or "").strip()
    return text, stats


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
async def call_model(cfg: ModelConfig, prompt: str) -> tuple[str, CallStats]:
    """Ask the model under test to answer `prompt`. Single HTTP call, no tools.

    Returns `(content, CallStats)`. `stats.finish_reason == "length"` means
    the model hit `max_tokens` before finishing — the runner counts that as
    a wrong answer.
    """
    client = _get_client()
    t0 = time.time()
    resp = await client.chat.completions.create(
        model=cfg.model_id,
        temperature=0,
        top_p=1.0,
        max_tokens=MODEL_MAX_TOKENS,
        messages=[
            {"role": "system", "content": MODEL_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
    )
    latency = time.time() - t0
    if not resp.choices:
        raise EmptyChoicesError(f"empty choices in response from {cfg.model_id}")
    content = resp.choices[0].message.content or ""
    return content, _stats_from(resp, latency, cfg.price_in, cfg.price_out)


async def judge_answer(question: str, expected: str,
                       raw_response: str) -> tuple[bool, str, str, CallStats]:
    """Binary judge. Returns (is_correct, extracted_answer, full_judge_text, stats)."""
    if not raw_response.strip():
        return False, "N/A", "model returned empty response", CallStats()

    user = (
        f"Question:\n{question}\n\n"
        f"Expected answer: {expected}\n\n"
        f"Model response (head + tail; middle may be truncated):\n"
        f"{_excerpt_for_judge(raw_response)}"
    )
    text, stats = await _call_judge(JUDGE_SYSTEM_PROMPT, user, BINARY_JUDGE_MAX_TOKENS)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise BinaryJudgeParseError(
            "binary judge returned empty reply (would have silently scored 0)")

    # Judge is asked for exactly 3 lines (extracted / reason / YES|NO).
    # Be tolerant: extracted = first line, verdict = last line.
    extracted = lines[0]
    verdict = lines[-1].upper()
    return verdict.startswith("YES"), extracted, text, stats


async def judge_rubric_score(question: str, rubric: dict,
                             raw_response: str) -> tuple[float, str, list, CallStats]:
    """Rubric judge. Returns (raw_total, full_judge_text, breakdown, stats).
    Raises RubricJudgeParseError if the judge's reply can't be parsed.
    """
    if not raw_response.strip():
        return 0.0, "model returned empty response", [], CallStats()

    user = (
        f"Question:\n{question}\n\n"
        f"Rubric:\n{json.dumps(rubric, indent=2, ensure_ascii=False)}\n\n"
        f"Model response (head + tail; middle may be truncated):\n"
        f"{_excerpt_for_judge(raw_response)}"
    )
    text, stats = await _call_judge(
        RUBRIC_JUDGE_SYSTEM_PROMPT, user, RUBRIC_JUDGE_MAX_TOKENS,
        response_format={"type": "json_object"})

    raw_total, breakdown = _parse_rubric_judge_output(text, rubric)
    return raw_total, text, breakdown, stats
