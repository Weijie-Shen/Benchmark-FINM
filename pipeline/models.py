"""Model registry — every call routed through OpenRouter (one API key, one base URL).

The lineup mixes 2026-era frontier models with three deliberately weaker /
older baselines (`gpt-4`, `claude-3-haiku`, `gemini-2.5-flash`) so we can see
the gap between SOTA and yesterday's models on the same questions.

For each model we record whether OpenRouter's catalog says it accepts
`temperature` / `top_p`. Some reasoning models (e.g. `openai/gpt-5.5`) don't
— OpenRouter silently drops those params, so our "temperature=0 for
everyone" promise doesn't actually hold for them. We surface this in the
Result so the comparison can be qualified.

The `name` field is the display label used in CSV/JSON outputs.
The `model_id` field is OpenRouter's identifier.
"""
from dataclasses import dataclass


@dataclass
class ModelConfig:
    name: str                          # display name in results
    model_id: str                      # OpenRouter model ID
    supports_temperature: bool = True  # if False, temp/top_p are silently dropped


MODELS = [
    # Frontier / 2026-era
    ModelConfig("seed-2.0-lite",           "bytedance-seed/seed-2.0-lite"),
    ModelConfig("claude-sonnet-4.6",       "anthropic/claude-sonnet-4.6"),
    ModelConfig("gpt-5.5",                 "openai/gpt-5.5",                    supports_temperature=False),
    ModelConfig("gemini-3.1-flash-lite",   "google/gemini-3.1-flash-lite"),
    ModelConfig("grok-4.3",                "x-ai/grok-4.3"),
    ModelConfig("qwen3.6-flash",           "qwen/qwen3.6-flash"),
    ModelConfig("deepseek-v4-flash",       "deepseek/deepseek-v4-flash"),
    # Older / weaker baselines — for SOTA comparison
    ModelConfig("gpt-4",                   "openai/gpt-4"),
    ModelConfig("claude-3-haiku",          "anthropic/claude-3-haiku"),
    ModelConfig("gemini-2.5-flash",        "google/gemini-2.5-flash"),
]
