"""Model registry — every call routed through OpenRouter (one API key, one base URL).

For each model we record whether OpenRouter's catalog says it accepts
`temperature` / `top_p`. Some reasoning models (e.g. `claude-opus-4.7-fast`,
`openai/gpt-5.5`, `openai/gpt-chat-latest`) don't — OpenRouter silently drops
those params, so our "temperature=0 for everyone" promise doesn't actually hold
for them. We surface this in the Result so the comparison can be qualified.

The `name` field is the display label used in CSV/JSON outputs.
The `model_id` field is OpenRouter's identifier (verified against
https://openrouter.ai/api/v1/models on 2026-05-18).
"""
from dataclasses import dataclass


@dataclass
class ModelConfig:
    name: str                          # display name in results
    model_id: str                      # OpenRouter model ID
    supports_temperature: bool = True  # if False, temp/top_p are silently dropped


MODELS = [
    ModelConfig("claude-opus-4.7-fast",    "anthropic/claude-opus-4.7-fast",    supports_temperature=False),
    ModelConfig("claude-sonnet-4.6",       "anthropic/claude-sonnet-4.6"),
    ModelConfig("gpt-5.5",                 "openai/gpt-5.5",                    supports_temperature=False),
    ModelConfig("gpt-chat-latest",         "openai/gpt-chat-latest",            supports_temperature=False),
    ModelConfig("gemini-3.1-flash-lite",   "google/gemini-3.1-flash-lite"),
    ModelConfig("grok-4.3",                "x-ai/grok-4.3"),
    ModelConfig("kimi-k2.6",               "moonshotai/kimi-k2.6"),
    ModelConfig("qwen3.6-flash",           "qwen/qwen3.6-flash"),
    ModelConfig("deepseek-v4-flash",       "deepseek/deepseek-v4-flash"),
    ModelConfig("minimax-m2.7",            "minimax/minimax-m2.7"),
]
