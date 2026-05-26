# Quant Interview Benchmark — v3.7 Specification

**Status**: locked. Any change to the items below requires a version bump per §10.
**Spec date**: 2026-05-25.

This document describes the current locked experimental conditions. For the
history of how we got here (v3.0 tool support, v3.4 judge switch, v3.6 tool
disable, v3.7 robustness fixes, etc.), see [`CHANGELOG.md`](CHANGELOG.md).

---

## 1. Models

10 models, all reached via OpenRouter with one API key. The full list lives in
[`pipeline/models.py`](../pipeline/models.py). The lineup deliberately mixes
seven 2026-era frontier models with three older / weaker baselines (`gpt-4`,
`claude-3-haiku`, `gemini-2.5-flash`) so the SOTA gap is visible on the same
questions.

1 of the 10 (`gpt-5.5`) silently ignores the sampling parameters we send.
Its rows record `sampling_controlled=false` so the comparison can be
qualified.

---

## 2. Prompts

All three prompts live in [`pipeline/prompts.py`](../pipeline/prompts.py).
Edit there, not in code.

### Model under test — system prompt
```
You are answering a challenging quantitative interview question. Think step by step.
After your reasoning, clearly state your final answer at the end.
```

### Model under test — user prompt
Bare question text from `data/<category>.json`, no preamble.

### Model output contract — `Final Answer:` line

Every model is instructed to end its reply with a line of the form:

```
Final Answer: <committed answer>
```

or, if it cannot solve the problem:

```
Final Answer: I don't know
```

This contract exists so the binary judge has a deterministic anchor for
extraction. Without it, judges have to guess where a long reasoning trace
commits to a final value — which produced silent mis-grades in earlier runs
(judge returns empty, model gets a false 0). The judge looks for the LAST
`Final Answer:` occurrence first; only if missing does it scan the tail of
the response.

If the model hits `max_tokens` before writing the Final Answer line
(`finish_reason == "length"`), the cell is scored 0 and labeled
`extracted_answer="[truncated]"` — no judge call. This is a model-quality
failure, not a re-runnable pipeline error.

### Single prompt across all 5 categories — by design

We deliberately do **not** use category-specific system prompts (probability
vs brainteaser vs arithmetic vs coding vs finance). Reasons:

1. **Prompt quality becomes a hidden variable.** If our probability prompt is
   sharper than our finance prompt, score gaps reflect prompt engineering,
   not model ability. Reviewers cannot distinguish the two.
2. **Cross-model fairness.** Different model families respond differently to
   prompt style. A single neutral prompt removes the temptation to over-fit
   prompts to one vendor's preference.
3. **Real-world deployment uses one prompt.** Users don't switch prompts by
   topic; benchmarking should mirror that.
4. **Modern LLMs auto-detect topic.** Seeing `P(A|B)` or a function signature
   is enough cue for the model to switch frameworks; explicit hints are
   redundant.

This aligns with MMLU / GSM8K / MATH / HumanEval / MT-Bench, all of which use
a single prompt across heterogeneous tasks. Changing this rule requires a
v4.0 bump.

### Judge prompts
- Binary judge (§6.1) — 3-line plain-text output.
- Rubric judge (§6.2) — JSON object (enforced by `response_format`).

Judge model: `deepseek/deepseek-v4-pro` for both paths.

---

## 3. Inference / sampling parameters

| Parameter | Value | Notes |
|---|---|---|
| `temperature` | 0.0 | Honored by 9 of 10 models |
| `top_p` | 1.0 | Same |
| `max_tokens` (model) | 8192 | `finish_reason == "length"` → cell scored 0, no judge call |
| `max_tokens` (binary judge) | 1000 | Headroom for reasoning judges (deepseek-v4-pro) |
| `max_tokens` (rubric judge) | 1500 | Same |
| `seed` | not set | |
| `tools` | **not passed** | See §4 |
| `plugins` | **not passed** | See §4 |
| Reasoning effort | Model default | |
| Sampling strategy | Single pass | No majority vote, no tool loop |
| Per-HTTP-call timeout (model) | 120s | SDK raises `APITimeoutError` on overrun |
| Per-HTTP-call timeout (judge) | 60s | |
| SDK retries on 5xx / 429 / connection error | 3 | Exponential backoff |

---

## 4. Tools — all disabled

| Tool | Status |
|---|---|
| Calculator (function call) | ❌ Disabled |
| Web search | ❌ Disabled |
| Python code execution | ❌ Disabled |
| Function calling (`tools` param) | ❌ Disabled |
| Local RAG | ❌ Not implemented |

The benchmark measures **native reasoning** — the model answers from its own
parametric knowledge only. To experiment with tools, see the v3.0–v3.5 entries
in [`CHANGELOG.md`](CHANGELOG.md).

---

## 5. Dataset

Target: **5 categories × 10 questions = 50 total**.

| Category | `topic` value | Question style | Grading |
|---|---|---|---|
| Probability | `"probability"` | Word problems, distributions, expectations | Binary |
| Brain teaser | `"brainteaser"` | Classic puzzles | Binary |
| Arithmetic | `"arithmetic"` | Mental math, estimation | Binary |
| Coding | `"coding"` | Short coding / algorithm questions | Binary |
| Finance | `"finance"` | Open-ended valuation / market questions | **Rubric (1–10)** |

Only **finance** uses `answer_type: "open"` (rubric grading). All other
categories use binary grading (`number`, `string`, `choice`, or unspecified).

Per-question schema (binary):
```json
{
  "id": "b01",
  "topic": "brainteaser",
  "question": "How many trailing zeros in 100! ?",
  "answer": "24",
  "answer_type": "number"
}
```

Per-question schema (open / rubric): see §6.2 below and
[`data/finance.json`](../data/finance.json) for a worked example.

Required fields: `id`, `question`, `answer`. **Do not paraphrase or "clarify"
a question before adding it.** If a question is ambiguous, that ambiguity is
part of the test. Only fix encoding (`Ã` → `×`) and JSON-syntax errors.

The pipeline runs regardless of how many questions are populated — once a
category is filled in, it just appears in the output.

---

## 6. Grading

### 6.1 Binary judge (answer_types: `number`, `string`, `choice`, or unspecified)

Single `deepseek/deepseek-v4-pro` call per `(model, question)`. The judge sees
the question, the expected answer, and the model's response (head + tail
excerpt if oversized — see §6.3). It is instructed to:

1. Find the LAST `Final Answer:` line in the response and use what follows
   as the committed answer.
2. If none exists, scan only the last ~15 lines for a committed answer
   (often a `\boxed{...}` value or a bold final sentence).
3. If the model wrote `I don't know`, grade as incorrect.

The judge returns 3 plain-text lines:

- line 1: extracted answer (≤40 chars)
- line 2: reason (≤40 chars)
- line 3: `YES` or `NO`

`correct = (line 3 == "YES")`. Full text stored in `judge_reasoning`.
Contribution to total: `1.0` if correct else `0.0`.

If the judge returns an empty / unparseable reply, `BinaryJudgeParseError`
is raised and the cell is recorded as a re-runnable error (not as the model
scoring 0). The parser otherwise tolerates ≠3 lines by taking `extracted =
first line`, `verdict = last line`.

### 6.2 Rubric judge (answer_type: `open`)

When `answer_type == "open"`:

- The `answer` field is a **structured rubric** (JSON object), not a free-form
  string.
- A separate `judge_rubric_score` call replaces the binary judge.
- The judge sees the question, the rubric, and the model's full reply (head +
  tail excerpt if oversized). It scores each criterion individually, emitting
  a JSON object — enforced via `response_format={"type": "json_object"}`.
- Parser clamps each criterion's score to its declared max (defensive), then
  sums to get `raw_total`.
- **Contribution to total = `raw_total / rubric.total_points`** — so any
  rubric scale (5, 10, 100, anything) normalizes to a 0.0-1.0 contribution.
  Default total is 10.

If the judge reply can't be parsed (no JSON, malformed JSON, empty `scores`),
`RubricJudgeParseError` is raised; the row is recorded as a re-runnable error
(not as the model scoring 0).

#### Rubric schema (data side)

```json
{
  "id": "f01",
  "topic": "finance",
  "answer_type": "open",
  "question": "...",
  "answer": {
    "total_points": 10,
    "categories": [
      {
        "name": "Section name",
        "max_points": 5,
        "criteria": [
          {
            "id": "short_slug",
            "name": "Human-readable name",
            "points": 2,
            "description": "What the answer must contain to earn these points.",
            "trap": "Optional. Common error — if the model commits this, award 0 for this criterion."
          }
        ]
      }
    ]
  }
}
```

`points` may be integer or float (0.5 increments work well; smaller values
produce judge noise).

Validator enforces:
- `total_points == sum(category.max_points)` (float-epsilon comparison)
- For each category: `max_points == sum(criterion.points)`
- Criterion ids unique within a question

#### Judge output (JSON)

```json
{
  "scores": [
    {"id": "identical_prices_verdict", "score": 2,   "comment": "Stated clearly"},
    {"id": "risk_neutral_measure",     "score": 1,   "comment": "Vague"},
    {"id": "drift_replaced_by_r",      "score": 0.5, "comment": "Partial"}
  ],
  "total": 3.5,
  "summary": "<≤60 char overall verdict>"
}
```

The judge's `total` field is **ignored** — we recompute from the clamped
breakdown. Full judge text is stored in `Result.judge_reasoning`; the parsed
per-criterion list in `Result.rubric_breakdown`.

`Result.correct` is `None` for rubric questions (binary correctness doesn't
apply). The leaderboard sorts by `score_total = sum(Result.score)`.

#### How to write a good rubric

1. **Keep 2–4 categories**, not more — judge attention dilutes.
2. **Keep each category to 2–4 criteria** — same reason.
3. **Use point weights that reflect importance.** Verdict-style criteria
   (the bottom-line conclusion) should outweigh supporting concepts.
4. **Use 0.5 increments** for fine differentiation; avoid arbitrary fractions
   like 1.3.
5. **Write `description` to be checkable**: "Explicitly states X" beats
   "Demonstrates understanding of X".
6. **Use `trap` for known LLM failure modes** — calling them out lets the
   judge proactively penalize.
7. **Bad rubrics → noisy judge scores.** Quality is on the dataset author.

See [`data/finance.json`](../data/finance.json) for a worked example
(Black-Scholes / jumps question).

### 6.3 Judge excerpt policy

When the model's raw response exceeds `head + tail + 64` chars (defaults:
4000 + 4000), the middle is dropped and replaced with a marker. This keeps
the judge from missing a final answer placed near the top of a long reasoning
trace.

---

## 7. Evaluation protocol

| Setting | Value |
|---|---|
| Call granularity | 1 model call + 1 judge call per `(model, question)` |
| Question order | File order (concatenated per-category JSONs) |
| Concurrency | 8 (configurable via `--concurrency`) |
| Retry policy | SDK-level: 3 retries on 5xx / 429 / connection error |
| Crash safety | Each completed Result appended to `details_<ts>.jsonl` under a lock |
| Judge parse fallback | Binary: if reply isn't 3 lines, use first / last lines |

### Logged fields per (model, question) row

| Field | Description |
|---|---|
| `model` | Display name from `pipeline/models.py` |
| `question_id` | Question id |
| `score` | Float 0.0–1.0, contribution to the model's total |
| `correct` | Boolean for binary; `null` for rubric |
| `extracted_answer` | Binary: judge's line 1. Rubric: `"rubric:N/M"`. Truncated model output: `"[truncated]"` |
| `expected_answer` | String for binary; rubric dict for open |
| `raw_response` | Model's full reply |
| `judge_reasoning` | Full judge text |
| `latency_s` | Wall-clock seconds for the model call |
| `sampling_controlled` | `false` for the 1 model that ignores `temperature`/`top_p` |
| `rubric_score` | Raw rubric total (0..total_points) for open; `null` otherwise |
| `rubric_breakdown` | List of `{id, score, comment}` per criterion for open; `null` otherwise |
| `error` | `"ExceptionType: message"`, or `null` |

### Output files (per run, timestamped)

| File | Contents |
|---|---|
| `details_<ts>.json` | All Result rows, full info |
| `details_<ts>.jsonl` | Same rows, appended incrementally as each cell completes (crash-safe) |
| `summary_<ts>.json` | Per-model totals + per-category breakdown + `missed_ids` / `error_ids` |
| `scores_<ts>.csv` | Model × question matrix, sorted by total |

---

## 8. How to run

```bash
LLM/bin/python run_benchmark.py                       # full dataset
LLM/bin/python run_benchmark.py --questions data/finance.json   # one category
```

Estimated cost: **$0.50–$1.50** per full 10-model × 50-question run.
Estimated wall time: **5–30 minutes** at default concurrency 8.

---

## 9. Known limitations

1. **Small sample.** 50 target questions is enough for ranking, not for
   tight per-category statistical claims.
2. **Single judge model.** Every grading decision routes through
   `deepseek-v4-pro`. Audit `judge_reasoning` before publishing a
   leaderboard. Same-vendor pair (`deepseek-v4-flash` in the lineup, judged
   by `deepseek-v4-pro`) is a potential bias source.
3. **No tools, no multi-sample.** The benchmark measures one configuration.
   Real deployments differ.
4. **Training-data leakage risk.** Many classic interview questions appear
   on prep sites, so models may be remembering rather than reasoning. No
   defense against this in the current spec.
5. **1 model has uncontrolled sampling.** Flagged via
   `sampling_controlled=false` per row, but the comparison is inherently
   noisier for those models.
6. **Rubric judge can over-award.** Mitigated by clamping each criterion to
   its declared max and ignoring the judge's `total` field, but not eliminated.
7. **`Result.correct` is a partial signal.** Meaningful for binary rows only.
   Downstream analysis should sum `Result.score` (always 0.0–1.0) and ignore
   `correct` for rubric rows.

---

## 10. Versioning rules

| Change type | Bump |
|---|---|
| Add or remove a model from the lineup | v3.x |
| Add or edit dataset questions | v3.x |
| Add new logged field that doesn't alter behavior | v3.x |
| Pipeline robustness / error-handling fixes | v3.x |
| Add a new tool or change tool permissions | **v4.0** |
| Change prompt, sampling params, or scoring rule | **v4.0** |
| Change rubric schema or judge output format | **v4.0** |
