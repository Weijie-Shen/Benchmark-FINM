# Quant Interview Benchmark — v1.0 Specification

**Status**: locked for v1.0. Any change requires a version bump (v1.1 for non-breaking additions, v2.0 for changes that invalidate prior results).
**Spec date**: 2026-05-18.

---

## 0. Purpose

A binary-scored benchmark of LLM performance on **quantitative-finance interview questions** with **single verifiable answers**. v1.0 is a proof-of-concept run on a small set (10 questions × 10 models) to validate the pipeline and surface dataset issues before scaling up.

v1.0 is **not** intended to produce a publishable leaderboard — sample size is too small for statistical power. Its goal is to lock in the experimental conditions and catch issues (ambiguous questions, possible leakage, grader bugs) before v2.0 invests in a larger dataset.

---

## 1. Models (variables: model version, provider, routing, sampling support)

| Setting | v1.0 value |
|---|---|
| Number of models | 10 |
| Provider | All routed through **OpenRouter** (single API key) |
| Version pinning | All `model_id`s are **concrete versions** |
| Auto-routing | Disabled. Each `model_id` is a fixed OpenRouter endpoint. |
| Context window | Not enforced (all 10 ≥ 128k; v1.0 prompts < 2k tokens) |

### Models that **do not** honor our sampling parameters

Verified against `https://openrouter.ai/api/v1/models` on 2026-05-18.  For these models, OpenRouter silently drops `temperature` / `top_p`; they use their internal defaults. **The Result records `sampling_controlled=false` for each call against these models** so the comparison can be qualified.

- `anthropic/claude-opus-4.7-fast`
- `openai/gpt-5.5`
- `openai/gpt-chat-latest`

The canonical model list is `pipeline/models.py`:

```
anthropic/claude-opus-4.7-fast       (sampling NOT controlled)
anthropic/claude-sonnet-4.6
openai/gpt-5.5                       (sampling NOT controlled)
openai/gpt-chat-latest               (sampling NOT controlled)
google/gemini-3.1-flash-lite
x-ai/grok-4.3
moonshotai/kimi-k2.6
qwen/qwen3.6-flash
deepseek/deepseek-v4-flash
minimax/minimax-m2.7
```

---

## 2. Prompt setup (variables: system/user prompt, shot count, CoT, answer format)

| Setting | v1.0 value |
|---|---|
| Shot count | **Zero-shot** |
| System prompt | Brief — instructs step-by-step reasoning + `Final answer: X` on last line |
| User prompt | **Bare question text**, no preamble |
| CoT | **Encouraged** in system prompt, **not enforced** |
| Final-answer format | `Final answer: <value>` on the last line. (Extraction happens via LLM judge — see § 6.) |

Exact system prompt (in `pipeline/clients.py`):

> You are answering a quantitative finance interview question. Think step by step, then give your final answer on the LAST line in the form: Final answer: <value>

---

## 3. Inference / sampling parameters

| Parameter | v1.0 value | Notes |
|---|---|---|
| `temperature` | **0.0** | Honored by 7 of 10 models. The 3 reasoning models above silently drop it. |
| `top_p` | **1.0** | Same as above. |
| `max_tokens` | **8192** | Generous so reasoning models with long CoT aren't truncated |
| `seed` | not set | Not all models honor it |
| Reasoning effort / thinking budget | **Model default** | Not overridden; models in their natural mode |
| Sampling strategy | **Single pass** | No majority vote in v1.0 |

---

## 4. Tool permissions

| Tool | v1.0 |
|---|---|
| Web search | **Disabled** |
| Code execution (Python) | **Disabled** |
| Calculator | **Disabled** |
| RAG | **Disabled** |
| Function calling / tool calls | **Disabled** |

**Rationale**: v1.0 measures the model's **native reasoning ability** with no external assistance. Tool-augmented variants are deferred to a later version so we can compare matched conditions cleanly.

---

## 5. Dataset variables

| Setting | v1.0 value |
|---|---|
| Size | **10 questions** |
| Source | Hand-curated quantitative interview questions (provided by the user) |
| Format | JSON array in `data/questions.json` |
| Required fields | `id`, `question`, `answer`, `answer_type` |
| Optional fields | `topic`, `difficulty`, `tolerance` |
| `answer_type` allowed values | `number`, `string`, `choice` |
| Originality | Strongly prefer original or heavily-rewritten questions |
| Ambiguity policy | Two-interpretation questions rejected before lock |
| Locking | Dataset locked after human review, no edits mid-run |

The runner validates `questions.json` at startup: required fields present, IDs unique, `answer_type` in allowed set. A malformed dataset fails fast before any API call is made.

---

## 6. Answer extraction + scoring

### Answer extraction — LLM judge

A separate Claude call is made per (model, question) pair to extract the model's final answer from its full response. The judge is given only the raw response (no question text, no expected answer) so it cannot bias toward correctness.

| Setting | v1.0 value |
|---|---|
| Judge model | `anthropic/claude-sonnet-4.6` |
| Judge sampling | temperature=0, top_p=1.0, max_tokens=100 |
| Judge prompt | "你从下面的模型回复中抽取一个具体的最终答案。只返回答案本身（一个数字、一个字符串、或一个选项字母），不要解释，不要复述题目，不要写单位或约等于号。如果回复中没有给出明确答案，返回 N/A。" |
| Judge input | Only the evaluated model's raw response (no question, no expected answer — judge has no way to bias toward correctness) |
| Judge output | A clean answer string, or "N/A" |
| Cost | ~100 extra calls per run (one judge call per (model, question)). Sonnet is cheap. |

### Scoring rules

| `answer_type` | Rule |
|---|---|
| `number` | Parse both extracted and expected as floats; absolute diff ≤ `tolerance` (default 1e-4). Parser handles decimals, fractions (`a/b`), percentages (`16.67%` → 0.1667), and LaTeX `\frac{a}{b}`. |
| `string` | Word-boundary, case-insensitive match: expected appears as a whole word in extracted (`yes` matches `Yes, they are independent.`). |
| `choice` | First letter A–E in extracted (case-insensitive) equals expected. |

| Other | v1.0 value |
|---|---|
| Per-question scoring | Binary 0/1 |
| Partial credit | None |
| Human review | 100% of extractions where `extracted_answer="N/A"` + ~20% spot check |

---

## 7. Evaluation protocol

| Setting | v1.0 value |
|---|---|
| Call granularity | One API call per question per model + one judge call per response |
| Question order | ID-sorted (independent calls → no order effect) |
| Retry policy | **None in v1.0**. Errors logged and (model, question) cell recorded as failure. |
| Concurrency | 8 simultaneous in-flight calls (configurable via `--concurrency`) |

### Logged fields per (model, question) call

| Field | Description |
|---|---|
| `model` | Display name from `pipeline/models.py` |
| `question_id` | Question ID |
| `correct` | Boolean — judge's extracted answer matches expected per the rules above |
| `extracted_answer` | What the judge pulled out (or `"N/A"`) |
| `expected_answer` | Dataset's reference answer |
| `raw_response` | Full unmodified model output |
| `latency_s` | Wall-clock seconds for the **model** call (does not include judge call) |
| `sampling_controlled` | `false` if the model is one of the 3 that drop `temperature` / `top_p` |
| `error` | Error type + message, or `null` |

### Output files (per run, timestamped)

| File | Contents |
|---|---|
| `details_<ts>.json` | One row per (model, question) — all logged fields |
| `summary_<ts>.json` | Per-model totals + `sampling_controlled` flag |
| `scores_<ts>.csv` | Model × question matrix + score + accuracy + sampling_controlled |

---

## 8. How to run

```bash
pip install -r requirements.txt
cp .env.example .env             # then fill OPENROUTER_API_KEY in .env (not .env.example)
python run_benchmark.py
```

Mock mode has been removed in v1.0 — all runs hit real APIs.

---

## 9. Known v1.0 limitations

1. **Small sample (10 questions)** — high variance; treat the leaderboard as directional only.
2. **No tool use** — doesn't reflect realistic LLM usage today. Deferred to v1.1+.
3. **Training-data contamination risk** — classic interview problems may be memorized.
4. **No retry on transient errors** — a single API failure leaves that cell as `error`.
5. **3 of 10 models ignore sampling parameters** — qualified in results via `sampling_controlled`.
6. **Judge model is itself an LLM** — could occasionally misextract. Human review of edge cases is required before publishing.
7. **Judge cost** — one extra LLM call per (model, question), roughly doubling API request count (cheap because the judge is Sonnet with max_tokens=100).

---

## 10. Versioning rules

| Change type | Bump |
|---|---|
| Add or remove a model from the lineup | v1.x |
| Add or edit dataset questions | v1.x |
| Change prompt, sampling params, scoring rule, judge model, or tool permission | **v2.0** |
| Add new logged field that doesn't alter behavior | v1.x |
