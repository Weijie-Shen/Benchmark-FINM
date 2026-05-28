# Quant Interview Benchmark — v3.12 Specification

**Status**: locked. Any change to the items below requires a version bump per §10.
**Spec date**: 2026-05-27.

This document describes the current locked experimental conditions. For the
history of how we got here (v3.0 tool support, v3.4 judge switch, v3.6 tool
disable, v3.7–v3.10 robustness fixes, v3.11 pluggable judge + numeric
pre-check, v3.12 parser fixes + 5-judge experiment), see
[`CHANGELOG.md`](CHANGELOG.md).

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
vs brainteaser vs machine_learning vs corporate_finance vs derivatives). Reasons:

1. **Prompt quality becomes a hidden variable.** If our probability prompt is
   sharper than our derivatives prompt, score gaps reflect prompt engineering,
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
- Binary judge (§6.1) — 3-line plain-text output. Opens with an explicit
  "YOU ARE A COMPARATOR, NOT A SOLVER" block (v3.12) to suppress
  re-derivation behavior observed in Sonnet on hard cells. Includes
  concrete few-shot examples of numeric tolerance, inequality
  literalism, and hedged-commit handling.
- Rubric judge (§6.2) — JSON object (enforced by `response_format`).
  Opens with a "YOU ARE A GRADER, NOT A SOLVER" block; explicitly
  forbids chain-of-thought preamble before the JSON.

### Judge model — pluggable

Default judge: `deepseek/deepseek-v4-pro` (the strict reasoning judge).
Override at run time with `--judge <openrouter-id>` on any entry script.
Models with verified pricing live in `KNOWN_JUDGE_PRICING` in
`pipeline/clients.py`:

| Judge | Pricing ($/Mtok in/out) | Character |
|---|---|---|
| `google/gemini-3.1-flash-lite` | 0.250 / 1.500 | Fast, lenient, non-reasoning |
| `deepseek/deepseek-v4-pro` (default) | 0.435 / 0.870 | Strict reasoning |
| `qwen/qwen3.6-flash` | 0.188 / 1.125 | Reasoning-heavy (~15K reasoning tokens/rubric) |
| `x-ai/grok-4.3` | 1.250 / 2.500 | Strict reasoning; non-US lineage |
| `anthropic/claude-sonnet-4.6` | 3.000 / 15.000 | Lenient mid-strict reasoning |
| `openai/gpt-5.5` | 5.000 / 30.000 | **Not recommended** — `supports_temperature=False` ⇒ non-deterministic verdicts |

Adding a new judge means adding one line to `KNOWN_JUDGE_PRICING`.

---

## 3. Inference / sampling parameters

| Parameter | Value | Notes |
|---|---|---|
| `temperature` | 0.0 | Honored by 9 of 10 models |
| `top_p` | 1.0 | Same |
| `max_tokens` (model) | 8192 | `finish_reason == "length"` → cell scored wrong, no judge call |
| `max_tokens` (binary judge) | 4000 | Generous headroom for reasoning judges (deepseek burns 200–500 reasoning tokens; qwen 600+) |
| `max_tokens` (rubric judge) | 5000 | Same, larger since rubric output has more fields |
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
| Machine learning | `"machine_learning"` | Linear regression, Bayes, statistical estimation | Binary |
| Corporate finance | `"corporate_finance"` | MCQ on capital structure / governance + time-value calculations | Binary (incl. MCQ) |
| Derivatives | `"derivatives"` | Open-ended options / pricing questions | **Rubric (1–10)** |

Only **derivatives** uses `answer_type: "open"` (rubric grading). All other
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
[`data/derivatives.json`](../data/derivatives.json) for a worked example.

Required fields: `id`, `question`, `answer`. **Do not paraphrase or "clarify"
a question before adding it.** If a question is ambiguous, that ambiguity is
part of the test. Only fix encoding (`Ã` → `×`) and JSON-syntax errors.

The pipeline runs regardless of how many questions are populated — once a
category is filled in, it just appears in the output.

---

## 6. Grading

### 6.1 Binary judge (answer_types: `number`, `string`, `choice`, or unspecified)

The binary path runs in two stages.

**Stage 1 — Numeric pre-check (deterministic, no API call)** (v3.11+):

If both `expected` and the model's committed answer (extracted from the
LAST `Final Answer:` line) parse as clean numerics — plain decimal,
percent, simple fraction `a/b`, or currency `$199,900.46` — the pipeline
compares them in Python directly. Agreement within `rel_tol=1e-4` (or
`abs_tol=1e-9`, whichever is larger) auto-scores YES without calling
the judge. Tolerance was tightened from 0.5% to 0.01% after a safety
audit against judge-unanimous cells caught 10 false positives at the
looser bound. Pre-check fires on ~25 % of binary cells in the current
dataset and eliminates judge-model variance for those.

On disagreement, the call **falls through to the judge** — equivalent
forms (algebraic expressions, prose) and multi-fact answers can still
be recognized.

**Stage 2 — LLM judge call** (when pre-check doesn't fire):

Single API call per `(model, question)` to the configured judge model.
The judge sees the question, the expected answer, and the model's
response (head + tail excerpt if oversized — see §6.3). It is
instructed to:

1. Find the LAST `Final Answer:` line in the response and use what follows
   as the committed answer.
2. If none exists, scan only the last ~15 lines for a committed answer
   (often a `\boxed{...}` value or a bold final sentence).
3. If the model wrote `I don't know`, grade as incorrect.

The judge is required to output exactly 3 lines:

- line 1: extracted answer (≤60 chars)
- line 2: reason (≤60 chars)
- line 3: `YES` or `NO`

**Verdict parsing** (v3.12 rewrite): `_verdict_is_yes(text)` scans the
text from the bottom up for word-boundary `\bYES\b` or `\bNO\b`
tokens, returning the first match. This correctly handles common
label-prefixed forms: `"Line 3: YES"`, `"**YES**"`, `"Verdict: NO."`.
Word boundaries exclude incidental substrings like `know`, `NOT`, or
`No such X exists`. If neither token appears anywhere,
`BinaryJudgeParseError` is raised (re-runnable cell error).
`_strip_label()` cleans `Line 1:` / `Extracted:` prefixes from
`extracted_answer` so the Result row is consistent across judges.

The contribution to total is `1.0` if YES else `0.0`. Full judge text
stored in `judge_reasoning`. For pre-check hits, `judge_reasoning`
starts with `"numeric pre-check:"` for easy filtering.

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

**Tolerant JSON parsing** (v3.11–v3.12). Real judges emit JSON with
several observable formatting quirks. `_parse_rubric_judge_output`
composes four fallbacks in order, returning the first that yields a
dict with a `"scores"` key (whitespace in keys is normalized):

1. `_loads_tolerant`: strict `json.loads`, then retry after stripping
   trailing commas before `]` or `}` (gemini-flash-lite emits these).
2. Fenced ```` ```json ```` block (some providers wrap JSON in
   markdown).
3. `_extract_object_with_key`: walks `text` yielding each balanced
   top-level `{...}` block, returns the first that parses to a dict
   with `"scores"`. Handles Sonnet's chain-of-thought-then-JSON
   pattern even when the prose contains literal `{...}` fragments
   like LaTeX or example dicts.
4. **Non-dict items in `"scores"` are skipped** (v3.12). Grok-4.3
   stuffs a stray `"summary: ..."` string into the scores list
   alongside the real criterion dicts; the parser uses the dicts and
   skips the string. If NO dict items survive, raises
   `RubricJudgeParseError`.

If all four fallbacks fail, `RubricJudgeParseError` is raised; the
row is recorded as a re-runnable error (not as the model scoring 0).

#### Rubric schema (data side)

```json
{
  "id": "d01",
  "topic": "derivatives",
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

See [`data/derivatives.json`](../data/derivatives.json) for a worked example
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
| `score` | Float 0.0–1.0, contribution to the model's total. Binary: 0 or 1; rubric: `raw_total / total_points` |
| `correct` | Boolean for binary; `null` for rubric |
| `extracted_answer` | Binary: judge's line 1 with `Line 1:` / `Extracted:` / markdown emphasis stripped (v3.12). Pre-check hit: the model's committed answer text. Rubric: `"rubric:N/M"`. Empty model response: `"N/A"` |
| `expected_answer` | String / number for binary; list of letters for MCQ; rubric dict for open |
| `raw_response` | Model's full reply |
| `judge_reasoning` | Full judge text (3-line plain for binary; JSON object for rubric) |
| `sampling_controlled` | `false` for the 1 model that ignores `temperature`/`top_p` |
| `model_latency_s` | Wall-clock seconds for the model API call (TCP/TLS + queue + inference + response) |
| `judge_latency_s` | Wall-clock seconds for the judge API call |
| `latency_s` | `model_latency_s + judge_latency_s`. Does NOT include semaphore-wait time |
| `model_input_tokens` | Prompt tokens consumed by the model |
| `model_output_tokens` | Full completion budget consumed — **includes reasoning_tokens**; billing is on this |
| `model_reasoning_tokens` | Subset of output_tokens that was internal thinking. `0` if the provider doesn't report it |
| `model_finish_reason` | `"stop"` / `"length"` / `"content_filter"` / ... `"length"` means the cell is counted as wrong |
| `judge_input_tokens` / `judge_output_tokens` / `judge_reasoning_tokens` | Same semantics, for the judge call |
| `model_cost_usd` | `(input × price_in + output × price_out) / 1M` |
| `judge_cost_usd` | Same, for the judge |
| `has_final_answer_line` | `true` if the model's last ~500 chars contain `"Final Answer:"` — audits output-contract compliance |
| `rubric_score` | Raw rubric total (0..total_points) for open; `null` otherwise |
| `rubric_breakdown` | List of `{id, score, comment}` per criterion for open; `null` otherwise |
| `error` | `"ExceptionType: message"`, or `null` |

### Output files (per run, labeled or timestamped)

The suffix is `--label`'s argument when given, else the current
timestamp `YYYYMMDD-HHMMSS`. Files are never overwritten — each script
writes a NEW labeled set.

| File | Contents |
|---|---|
| `details_<label>.json` | All Result rows, full info |
| `details_<label>.jsonl` | Same rows, appended incrementally as each cell completes (crash-safe) |
| `summary_<label>.json` | Per-model totals + per-category breakdown + `missed_ids` / `error_ids` |
| `scores_<label>.csv` | Model × question matrix, sorted by total |

For cross-judge experiments, the recommended naming convention is
`details_run<N>_<judge>.json` (e.g. `details_run3_deepseek.json`).
`make_report.py` auto-detects this pattern and groups files by run
number.

---

## 8. How to run

```bash
LLM/bin/python run_benchmark.py                                # full dataset, default deepseek judge
LLM/bin/python run_benchmark.py --questions data/derivatives.json   # one category
LLM/bin/python run_benchmark.py --judge anthropic/claude-sonnet-4.6 --label run3_sonnet   # alt judge

# Cross-judge experiment (no model re-calls; re-uses preserved raw_response)
LLM/bin/python rejudge_run.py \
    --details results/details_run3_deepseek.json \
    --judge x-ai/grok-4.3 --label run3_grok

# Report figures (auto-detects all judge files for the highest-numbered run)
LLM/bin/python make_report.py --canonical-judge deepseek
```

Cost (observed, run3 with default deepseek judge): **~$5** total
(~$4 model, ~$0.50 judge). Adding cross-judge rejudge passes:
$0.23–$2.92 per judge depending on model (see README cost table).
Wall time: **10–25 minutes** for the initial run; **3–5 minutes** per
rejudge pass.

---

## 9. Known limitations

1. **Small sample.** 50 target questions is enough for ranking, not for
   tight per-category statistical claims.
2. **Cross-judge variance is real but measurable.** The default judge
   is `deepseek-v4-pro`. Run3 with 5 judges (gemini, deepseek, grok,
   sonnet, qwen) measured the spread on the same model outputs:
   binary cells split on only ~6% of cells (well-tamed); rubric
   (derivatives) cells show a 16-point spread across judges (still
   structural). For published claims, either use deepseek as the
   strict canonical (recommended; what `make_report.py
   --canonical-judge deepseek` produces) or report multi-judge
   median. Same-vendor pairs (`deepseek-v4-flash` model judged by
   `deepseek-v4-pro`) are not strong bias sources in the observed
   data, but worth flagging.
3. **No tools, no multi-sample.** The benchmark measures one configuration.
   Real deployments differ.
4. **Training-data leakage risk.** Many classic interview questions appear
   on prep sites, so models may be remembering rather than reasoning. No
   defense against this in the current spec.
5. **1 model has uncontrolled sampling.** `gpt-5.5` ignores
   `temperature` / `top_p`. Flagged via `sampling_controlled=false`
   per row. For the same reason `gpt-5.5` is **not allowed as a
   judge** — verdicts would drift across re-runs.
6. **Rubric judge can over-award.** Mitigated by clamping each criterion to
   its declared max and ignoring the judge's `total` field, but not
   eliminated. Rubric judging is the largest remaining variance source.
7. **`Result.correct` is a partial signal.** Meaningful for binary rows only.
   Downstream analysis should sum `Result.score` (always 0.0–1.0) and ignore
   `correct` for rubric rows.
8. **Sonnet occasionally produces no verdict** on hard cells (writes
   extended reasoning then truncates). v3.12's anti-solving prompt
   reduces this but doesn't eliminate it; affected cells surface as
   `BinaryJudgeParseError` and are re-runnable.

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
