# Spec changelog

Version history for the Quant Interview Benchmark. The current spec lives in
[`SPEC_v3.md`](SPEC_v3.md); v2.0 and earlier are archived under
[`archive/`](archive/). Entries are newest-first.

---

## v3.12 — judge robustness fixes + 5-judge run3 + report generator (2026-05-27)

Cross-judge work on run3 surfaced several latent parser bugs and a
prompt failure mode in Sonnet. This version closes those plus adds a
report-figure generator.

Pipeline robustness:

- **Binary verdict parser fix** (`pipeline/clients.py`). The previous
  check `lines[-1].upper().startswith("YES")` silently scored as NO any
  judge output where the verdict was prefixed: `"Line 3: YES"`,
  `"**YES**"`, `"Verdict: YES."`. Replaced with `_verdict_is_yes(text)`
  which scans bottom-up for `\bYES\b` / `\bNO\b` tokens. On the existing
  run2 data this fix would have flipped 14 cells from wrongly-NO to
  correctly-YES (5 deepseek + 9 sonnet). Raises `BinaryJudgeParseError`
  if no YES/NO appears anywhere — surfaces judge-failure cases that
  used to be hidden as silent NOs.

- **Label-stripping for `extracted_answer`** (`_strip_label` in
  `clients.py`). Removes `Line 1:`, `Extracted:`, `Verdict:`, and outer
  markdown emphasis (`**...**`, `__...__`, `*...*`, `_..._`) so the
  Result row's extracted_answer stays clean across judges.

- **Tolerant rubric parser, third fallback** (`_parse_rubric_judge_output`).
  Grok-4.3 emits a `"summary: ..."` STRING inside the `"scores"` list
  (instead of as a top-level `"summary"` key). The parser now skips
  non-dict items instead of crashing with `AttributeError: 'str' object
  has no attribute 'get'` — which was killing the whole orchestrated
  run, not just one cell. If no dict items survive, raises
  `RubricJudgeParseError`.

- **Anti-solving prompt guards** (`pipeline/prompts.py`). Both
  `JUDGE_SYSTEM_PROMPT` and `RUBRIC_JUDGE_SYSTEM_PROMPT` open with a
  "YOU ARE A COMPARATOR/GRADER, NOT A SOLVER" block. Sonnet had been
  re-deriving expected answers for hard problems and sometimes
  producing essays with no YES/NO verdict at all (7 such cells in run2
  sonnet, 1 in run3 sonnet). The new guard explicitly forbids the
  "Looking at this problem..." / "Let me verify..." preamble pattern
  and ends the binary prompt with an action-consequence reminder.

- **New judge in registry**: `qwen/qwen3.6-flash` at $0.188/$1.125 per
  Mtok. ⚠️ Despite the "flash" name, qwen does extensive internal
  reasoning (~15 K reasoning tokens per rubric cell observed), making
  actual cost ~$2.07 for a full rejudge — closer to sonnet than to
  gemini-flash-lite. Documented in the README cost table.

Dataset revisions:

- **`data/brainteaser.json`**: 7 of 10 questions revised to use
  single-token expected answers (`number` answer_type where possible).
  Multi-fact strategy/answer combos (e.g. "14 tests in the worst case.
  Use decreasing interval sizes...") consolidated to just the number.
  These cells now hit the numeric pre-check fast-path → no judge
  variance.

- **`data/machine_learning.json`** / ml07: expected answer changed
  from `"N > log(1 - p) / log(1 - (1 - e)^s)"` to
  `"N = ceil(log(1-p) / log(1-(1-e)^s))"`. The original strict
  inequality was the dominant source of ML disagreements in run2 (8 of
  15 ML disagreement cells were ml07 with judges splitting on
  `>` vs `≥` vs `ceil(...)` literalism). The ceil form is the
  idiomatic textbook answer and unambiguous.

New tooling:

- **`make_report.py`** — report-figure generator. Loads any number of
  `details_run<N>_<judge>.json` files (default: auto-detects the
  highest-numbered run) and emits 5 PNGs to `report_figures/`:
  leaderboard, per-category heatmap, per-category cross-judge spread,
  per-cell rubric spread (top-N by spread), N-way agreement
  breakdown. `--canonical-judge <name>` switches the leaderboard +
  heatmap to a single-judge view (no error bars) while the comparison
  charts stay multi-judge. Adds `matplotlib==3.10.9` to
  `requirements.txt`.

- **Smarter filename label inference.** Supports both old
  `details_runN_judge_X.json` and new `details_runN_X.json` naming
  conventions. The default glob picks the latest run automatically;
  passing `--inputs` explicitly still works.

Cross-judge experiment results (run3):

- `results/details_run3_{deepseek,gemini,grok,sonnet,qwen}.json` —
  same 500 model outputs, 5 judges. Total points awarded:
  sonnet=372.25 > gemini=358.70 > qwen=355.00 > deepseek=345.55 >
  grok=338.90. Binary cells split (any disagreement) on only 6.4% of
  cells across 5 judges; derivatives (rubric) shows a 16.35-point
  cross-judge spread, structurally judge-dependent.

- `gpt-5.5` was considered as a 5th judge but rejected — OpenRouter's
  catalog lists `supports_temperature=False` for it, meaning verdicts
  would be non-deterministic across runs.

## v3.11 — cross-judge experimentation: pluggable judge + numeric pre-check (2026-05-27)

Motivated by observing meaningful judgement variance across judges on the
same model outputs. Adds infrastructure to run multi-judge experiments and
narrows the cross-judge gap on the deterministic numeric path.

- **`--judge <openrouter-id>` flag** on all entry scripts. Default judge
  is unchanged (`deepseek/deepseek-v4-pro`); the override sets the model
  via the new `clients.set_judge()` helper, which looks up pricing in
  `KNOWN_JUDGE_PRICING`. Known judges so far: deepseek-v4-pro,
  gemini-3.1-flash-lite, openai/gpt-5.5, claude-sonnet-4.6, x-ai/grok-4.3.
- **`rejudge_run.py`** (new) — fourth entry script. Re-judges every cell of
  an existing `details_*.json` with a (possibly different) judge, reusing
  each cell's preserved `raw_response`. No model re-calls — cheap and
  isolates the judge effect: same outputs, two judges → any score delta
  is the judge's. Used for the run2 × {gemini, deepseek, sonnet} comparison.
- **Numeric pre-check (deterministic fast-path).** `judge_answer` now
  attempts to parse both expected and the model's committed answer (from
  the `Final Answer:` line) as clean numeric forms — plain decimal,
  percent, simple fraction `a/b`, currency `$199,900.46`. If both parse
  and agree within 1e-4 relative tolerance (or 1e-9 absolute), the cell
  auto-passes — no judge call. Skips ~20% of binary cells (~95 of 400 in
  the current dataset). Tolerance is deliberately tight: a safety check
  against the 357 cells both gemini and deepseek agreed on showed 0 false
  positives. Disagreements still fall through to the judge.
- **Strengthened binary-judge prompt** with concrete few-shot examples
  for: numeric-equivalence (0.647 ≈ 0.6475 → YES), fraction-vs-decimal
  (90/139 ≈ 0.6475 → YES), inequality literalism (≥ vs > → NO), hedged
  multi-commit ("64.7% or 0.65" → use the first value), and
  Rule-of-72-style approximations (200000 vs 202237 → NO).
- **Tolerant rubric-JSON parser**, three new fallbacks composed in
  `_parse_rubric_judge_output`:
  1. `_loads_tolerant` strips trailing commas before retrying
     `json.loads` (gemini-flash-lite emits these).
  2. Whitespace normalisation of object keys (deepseek sometimes
     emits `" scores"` instead of `"scores"`).
  3. `_extract_object_with_key` walks the text yielding each top-level
     balanced `{...}` block and returns the first that parses to a dict
     with the expected key (sonnet emits chain-of-thought prose then a
     bare JSON object).

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
