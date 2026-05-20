"""OpenRouter client + LLM-judge answer extractor.

All 10 evaluated models AND the judge (Claude Sonnet) are reached through one
AsyncOpenAI client pointed at OpenRouter's OpenAI-compatible endpoint.

Two entry points used by the runner:
    call_model(cfg, prompt) -> str          # query an evaluated model
    judge_extract(raw_response) -> str      # ask the judge to extract the answer
"""
from __future__ import annotations

import os

from .models import ModelConfig


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# System prompt sent to the model being evaluated.
SYSTEM_PROMPT = (
    "You are answering a quantitative finance interview question. "
    "Think step by step, then give your final answer on the LAST line "
    "in the form: Final answer: <value>"
)

# Judge model — used only to extract the model's final answer from its
# response. Must support temperature=0 (Sonnet does; opus-4.7-fast does NOT).
JUDGE_MODEL = "anthropic/claude-sonnet-4.6"

# Prompt for the judge. Kept short and strict so we get a single token-ish
# answer back, not paragraphs.
JUDGE_SYSTEM_PROMPT = (
    "你从下面的模型回复中抽取一个具体的最终答案。"
    "只返回答案本身（一个数字、一个字符串、或一个选项字母），"
    "不要解释，不要复述题目，不要写单位或约等于号。"
    "如果回复中没有给出明确答案，返回 N/A。"
)


# ---------------------------------------------------------------------------
# OpenRouter client (lazy singleton)
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
        default_headers={
            "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", ""),
            "X-Title":      os.environ.get("OPENROUTER_TITLE", "Quant Interview Benchmark"),
        },
    )
    return _client


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
async def call_model(cfg: ModelConfig, prompt: str) -> str:
    """v1.0 sampling: temperature=0, top_p=1, max_tokens=8192, no tool use.

    For models with `supports_temperature=False`, OpenRouter silently drops
    those params; the model uses its built-in default. This is recorded
    per-Result so the comparison can be qualified later.
    """
    client = _get_client()
    resp = await client.chat.completions.create(
        model=cfg.model_id,
        temperature=0,
        top_p=1.0,
        max_tokens=8192,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content or ""


async def judge_extract(raw_response: str) -> str:
    """Use the judge model to extract the model's final answer. Returns 'N/A' if none."""
    if not raw_response or not raw_response.strip():
        return "N/A"
    client = _get_client()
    resp = await client.chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0,
        top_p=1.0,
        max_tokens=100,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user",   "content": f"模型回复：\n{raw_response}"},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    return text or "N/A"


def has_api_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))
