# Spec changelog

Version history for the Quant Interview Benchmark. The current spec lives in
[`SPEC_v3.md`](SPEC_v3.md); v2.0 and earlier are archived under
[`archive/`](archive/). Entries are newest-first.

---

## v3.7 — pipeline robustness fixes (2026-05-25)

- **Judge excerpt is now head + tail, not tail-only.** The model's raw response
  is truncated to first 4000 + last 4000 chars (with a marker for the dropped
  middle), preventing false negatives when a reasoning model puts its final
  answer near the beginning before rambling.
- **SDK `max_retries` set to 3 explicitly** (was using SDK default of 2). The
  OpenAI SDK retries 5xx / 429 / connection errors automatically with
  exponential backoff.
- **Incremental JSONL save.** Every completed `(model, question)` Result is
  appended to `results/details_<ts>.jsonl` as it finishes. If the process
  crashes mid-run, the jsonl preserves all completed cells.
- **Narrower exception catch in the runner.** Only `openai.APIError` (which
  covers `APITimeoutError`) and `RubricJudgeParseError` are recorded as
  cell-level errors. Everything else propagates so pipeline bugs surface
  loudly.
- **`RubricJudgeParseError`** (new): raised when the rubric judge's reply has
  no parseable JSON, malformed JSON, or empty `scores` list. Treated as a
  re-runnable error row, not as the model scoring 0.
- **Dropped `Result.tool_calls`** — it was always `None` since v3.6 and only
  kept for back-compat. Removed from the schema.

## v3.6 — all tools disabled (2026-05-24)

- **All tools DISABLED** — calculator function call and web-search plugin both
  turned off. Reverts to a pure native-reasoning configuration (similar to
  v2.0) while keeping all v3.x rubric / judge / scoring improvements.
- `MODEL_SYSTEM_PROMPT` simplified: dropped mentions of calculator and web
  search.
- `call_model` returns a plain `str` (was `tuple[str, list]`); the
  tool-dispatch loop, `CALCULATOR_TOOL`, `WEB_PLUGIN`, `_safe_calc`, and
  `MAX_TOOL_ITERATIONS` constants are all removed from `pipeline/clients.py`.
- `Result.tool_calls` is always `None` (kept on the schema for backward compat
  with v3.0–v3.5 detail files; later dropped in v3.7).

## v3.5 — model prompt de-biased (2026-05-23)

- **Model system prompt de-biased**: `"quantitative-finance interview
  question"` → `"challenging quantitative interview question"`. The old
  `"finance"` framing was wrong for brainteaser / coding categories and risked
  anchoring models into the wrong reasoning style.
- Explicit rationale added to §2 for the single-prompt-across-categories
  design decision (vs per-category prompts).

## v3.4 — judge model + rubric scale change (2026-05-22)

- **Judge model switched** from `anthropic/claude-sonnet-4.6` to
  **`deepseek/deepseek-v4-pro`** for cost (~10× cheaper). Used for BOTH the
  binary judge (3-line text output) and the rubric judge (JSON output).
- **Rubric scale switched from 100 → 10 points** (matches MT-Bench / industry
  norm; LLM judges show too much noise at 100-point granularity).
- **Criterion `points` may now be float** (0.5 increments encouraged for finer
  weighting at the 10-point scale). Validator uses float-epsilon when checking
  sum consistency.
- `Result.rubric_score` type changed from `int` to `float`.
- Note: `deepseek/deepseek-v4-flash` (in the eval lineup) is a different model
  from `deepseek-v4-pro`, but same vendor — finance scores for
  `deepseek-v4-flash` may carry residual same-vendor bias.

## v3.3 — structured rubric format (2026-05-21)

- **Structured rubric format.** For `answer_type: "open"` questions, the
  `answer` field is now a structured JSON object (not a free-form string).
  Each rubric defines `total_points`, a list of `categories`, and
  per-category `criteria` with point values and descriptions. Optional `trap`
  field per criterion calls out common mistakes.
- **No global `RUBRIC_WEIGHT`** — each rubric carries its own `total_points`,
  and contribution-to-total is normalized per question: `score = raw_total /
  total_points` (so any rubric scale produces a 0.0-1.0 contribution).
- **Rubric judge now emits JSON** inside a fenced ```` ```json ```` code
  block, with per-criterion scores. Parser clamps each criterion to its max
  and recomputes the total (don't trust the judge's arithmetic).
- New Result field `rubric_breakdown: list | None` stores the per-criterion
  scores for audit.
- Validator now structurally checks open-type rubrics: required fields,
  point-sum consistency (categories sum to total, criteria sum to category
  max), unique criterion ids.

## v3.2 amendment — dataset reorganized (2026-05-21)

- Dataset is now **one JSON file per category** under `data/`:
  `probability.json`, `brainteaser.json`, `arithmetic.json`, `coding.json`,
  `finance.json`.
- `difficulty` field dropped from all questions — never used for analysis,
  easy to misjudge subjectively, real difficulty is revealed by the results.
- Runner accepts `--questions data/` (directory, default) OR `--questions
  data/probability.json` (single file, for development iteration).
- No behavior / scoring / prompt changes — purely a file reorganization.

## v3.2 — per-question float score (2026-05-20)

- No thresholding for rubric questions; per-question `score: float`
  introduced.
- `correct` becomes `None` for rubric questions.
- `wrong_ids` renamed to `missed_ids`.
- Summary includes `per_category` breakdown.

## v3.1 — rubric judge path (2026-05-19)

- Rubric judge path added for `answer_type: "open"` questions.

## v3.0 — tools enabled (2026-05-18)

v2.0 measured **native reasoning** — all tools disabled. v3.0 turned tools on
to A/B against the v2.0 baseline and see how much tool use helps. (v3.6
reverted this; see above.)

| v2.0 | v3.0 |
|---|---|
| No tools — pure native reasoning | Calculator (function calling) + web search (OpenRouter plugin) |
| Single model call per question | Multi-turn: model may iterate up to 8 tool calls before final answer |
| `tool_calls` field absent on Result | Each tool invocation logged: name, arguments, result |
| Cost ~$1–2 per run | Estimated ~$3–6 per run (web search markup + multi-turn) |

> ⚠️ **Web search makes classic problems trivially solvable.** Many of our
> questions appear on quant interview prep sites; a model with web search
> looks up the canonical answer. This collapsed model discrimination — one
> reason v3.6 reverted to no tools.

For pre-v3.0 history see [`archive/SPEC_v2.md`](archive/SPEC_v2.md).
