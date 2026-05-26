# Quant Interview Benchmark — v2.0 Specification

**Status**: locked. Any change to the items below requires a version bump.
**Spec date**: 2026-05-20.

## Why v2.0 (vs v1.x)
v1.x mixed a code-based grader (regex + tolerance + word-boundary) with an LLM equivalence judge selected by `answer_type`. The code grader was a recurring source of false negatives — it didn't understand `√5`, `\frac{a}{b}` with annotations, percentages in some contexts, sentence-form string answers, etc. v2.0 collapses everything to a single path: every (model, question) pair is graded by Claude Sonnet 4.6 as judge.

| v1.x | v2.0 |
|---|---|
| Code grader (number / string / choice) + LLM judge (open) | LLM judge for **everything** |
| `answer_type` dispatches different graders | `answer_type` kept as informational metadata only |
| `tolerance` field controls numerical fuzz | Judge interprets numerical closeness; field ignored |
| `judge_extract` then separate `llm_equivalence_check` | One unified `judge_answer` call returns (verdict, extracted, reasoning) |

Results from v1.x and v2.0 are not directly comparable.

---

## 1. Models

| Setting | Value |
|---|---|
| Number of models | 10 |
| Provider | OpenRouter (single key) |
| Version pinning | All `model_id`s are concrete versions |
| Auto-routing | Disabled |

### Models that don't honor `temperature` / `top_p`

OpenRouter silently drops the sampling params for these; `sampling_controlled=false` is recorded on every Result row for them:

- `anthropic/claude-opus-4.7-fast`
- `openai/gpt-5.5`
- `openai/gpt-chat-latest`

Canonical list lives in `pipeline/models.py`.

---

## 2. Prompts

### Model under test — system prompt
```
You are answering a quantitative-finance interview question. Think step by step.
After your reasoning, clearly state your final answer at the end.
```

No strict "Final answer: X" format requirement (the judge sees the full reply, so a strict format would only penalize models with different answering styles).

### Model under test — user prompt
The bare question text from `data/questions.json`, no preamble.

### Judge — system prompt
```
You are a grading assistant. You receive a question, the expected answer, and a model's
full response. Decide whether the model's final answer is correct.

Grading principles:
1. Grade the model's final conclusion, not its intermediate reasoning.
2. Tolerate reasonable equivalent forms: numbers (0.667 ≈ 0.6667 ≈ 2/3), math symbols
   (√5 ≈ 2.236), fractions (1/4 ≈ 0.25 ≈ 25%), formulas (latex equivalence),
   time (11:23am ≈ 37 min before noon), semantics (down ≈ decreases ≈ water falls).
3. If the question asks several things, the model only needs to answer the part covered
   by the expected answer correctly.
4. If the model gave no identifiable final answer, write NO on line 3.

Output format (strictly 3 lines):
  Line 1: the model's final answer (≤30 chars)
  Line 2: brief reason for the verdict (≤30 chars)
  Line 3: YES or NO
```

### Judge — user prompt
```
Question: {question}
Expected answer: {expected}
Model response (full, may be truncated to last 4000 chars):
{raw_response_last_4000_chars}
```

---

## 3. Inference / sampling parameters

| Parameter | Value | Notes |
|---|---|---|
| `temperature` | 0.0 | Honored by 7 of 10 models |
| `top_p` | 1.0 | Same |
| `max_tokens` | 8192 | Generous so long CoT isn't truncated |
| `seed` | not set | Not all models honor it |
| Reasoning effort | Model default | Not overridden |
| Sampling strategy | Single pass | No majority vote |
| Per-call timeout | 180s model / 60s judge | Hung providers can't block the run |

---

## 4. Tool permissions

All disabled. v2.0 measures native reasoning.

| Tool | v2.0 |
|---|---|
| Web search | Disabled |
| Code execution | Disabled |
| Calculator | Disabled |
| RAG | Disabled |
| Function calling | Disabled |

---

## 5. Dataset

| Setting | Value |
|---|---|
| Size | 26 questions (11 brainteasers + 15 probability) |
| Format | JSON array in `data/questions.json` |
| Required fields | `id`, `question`, `answer` |
| Optional metadata | `topic`, `difficulty`, `answer_type` |
| Originality | User-curated; classic problems present (training-leakage risk noted) |
| Locking | Dataset locked before a benchmark run; no edits mid-run |

`answer_type` and `tolerance` (if present) are ignored by the grader — they exist for human navigation / future analysis only. All grading goes through the LLM judge regardless.

**Important rule for dataset editing**: never paraphrase a question to "clarify" it or add unstated assumptions. If a question is ambiguous, that ambiguity is part of the test. Fix only encoding artifacts (e.g. `Ã` → `×`) and JSON-syntax errors.

---

## 6. Grading — LLM-as-judge (the only path)

For every (model, question) pair:

1. Send the question to the model under test → get `raw_response`
2. Send `(question, expected, raw_response)` to the judge model → get a 3-line structured reply:
   - Line 1: the model's final answer, extracted verbatim (≤30 chars)
   - Line 2: a brief reason for the verdict (≤30 chars)
   - Line 3: `YES` or `NO`
3. Store `correct = (line 3 == YES)`, `extracted_answer = line 1`, `judge_reasoning = full 3-line text`

The judge is a single Claude Sonnet 4.6 call. It sees the full raw response (last 4000 chars). It does not see `answer_type` or `tolerance` — only the natural-language question, the expected answer, and the model's reply.

**Per-question scoring**: binary 0 / 1.
**Partial credit**: none.
**Human review**: required before publishing a leaderboard. Use `judge_reasoning` to spot-check the judge's decisions.

---

## 7. Evaluation protocol

| Setting | Value |
|---|---|
| Call granularity | 1 model call + 1 judge call per (model, question) |
| Question order | ID-sorted (independent calls → no order effect) |
| Retry policy | None in v2.0 |
| Concurrency | 8 simultaneous in-flight calls (configurable via `--concurrency`) |
| Output format errors | If the judge returns < 3 lines, parse defensively; verdict defaults to NO |

### Logged fields per (model, question) row

| Field | Description |
|---|---|
| `model` | Display name from `pipeline/models.py` |
| `question_id` | Question ID |
| `correct` | Boolean — judge's YES/NO |
| `extracted_answer` | Judge's line 1 — the model's final answer as the judge identified it |
| `expected_answer` | Dataset's reference answer |
| `raw_response` | Full unmodified model output |
| `judge_reasoning` | Full 3-line judge response (for audit) |
| `latency_s` | Wall-clock seconds for the model call (judge call not included) |
| `sampling_controlled` | `false` if the model drops `temperature` / `top_p` |
| `error` | Error type + message, or `null` |

### Output files (per run, timestamped)

| File | Contents |
|---|---|
| `details_<ts>.json` | One row per (model, question) — all fields above |
| `summary_<ts>.json` | Per-model totals + `wrong_ids` + `error_ids` + `sampling_controlled` |
| `scores_<ts>.csv` | Model × question matrix + score + accuracy + sampling flag |

---

## 8. How to run

```bash
LLM/bin/pip install -r requirements.txt          # one-time
cp .env.example .env                             # then fill OPENROUTER_API_KEY
LLM/bin/python run_benchmark.py                  # or: source LLM/bin/activate && python run_benchmark.py
```

Per run: 260 model calls + 260 judge calls = ~520 OpenRouter requests, ~$1–2.

---

## 9. Known v2.0 limitations

1. **Sample size 26** — still small; v2.x should grow it.
2. **No retry on transient errors** — a single API failure leaves that cell as `error`.
3. **Training-data contamination risk** — classic interview problems may be memorized.
4. **3 of 10 models can't have controlled sampling** — qualified via `sampling_controlled` flag.
5. **Judge accuracy is a single point of failure** — every grading decision depends on Sonnet. Audit `judge_reasoning` before publishing.
6. **No tool use** — doesn't reflect realistic LLM usage today.
7. **Some questions in the dataset are ambiguous (e.g. p03's "I pick a number" — is it uniform or adversarial?)**. By design, we do NOT paraphrase to disambiguate; the judge sees the question as-is. Ambiguity affects all models equally, so it doesn't bias the comparison — but it lowers the absolute scores.

---

## 10. Versioning rules

| Change type | Bump |
|---|---|
| Add or remove a model from the lineup | v2.x |
| Add or edit dataset questions | v2.x |
| Change prompt, sampling params, scoring rule, or tool permission | **v3.0** |
| Add new logged field that doesn't alter behavior | v2.x |
