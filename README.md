# Quant Interview Benchmark

A small, opinionated benchmark for measuring how well frontier LLMs do on
quantitative-interview questions: **probability, brain teasers, arithmetic,
coding, and finance**.

Target: 5 categories × 10 questions = 50 questions. Each question contributes
at most 1.0 point. A model's final score is the sum.

For the full experimental conditions (locked spec) see [`docs/SPEC_v3.md`](docs/SPEC_v3.md).

---

## Quick start

```bash
# One-time setup
python3 -m venv LLM
LLM/bin/pip install -r requirements.txt
cp .env.example .env                  # then paste your OPENROUTER_API_KEY into .env

# Run the full benchmark
LLM/bin/python run_benchmark.py

# Iterate on one category (e.g. while writing new finance rubrics)
LLM/bin/python run_benchmark.py --questions data/finance.json
```

Cost per full run: roughly **$0.50–$1.50** depending on question count.
Wall time: **5–30 minutes** with the default concurrency of 8.

---

## How it works

```
                                  +-------------------------------+
data/*.json (questions) --------> | for each (model, question):   |
                                  |                               |
                                  |   1. call_model    -> raw     |  one HTTP call,
                                  |                               |  no tools
                                  |                               |
                                  |   2a. binary path             |
                                  |       judge_answer            |  -> YES / NO
                                  |   2b. open path (rubric)      |
                                  |       judge_rubric_score      |  -> 0..total_points
                                  |                               |
                                  |   3. score in [0.0, 1.0]      |
                                  +---------------+---------------+
                                                  |
                                                  v
                          results/{details,summary,scores}_<timestamp>.{json,csv}
```

**Two scoring paths**, dispatched on each question's `answer_type`:

| `answer_type` | Used by | Judge output | Contribution to total |
|---|---|---|---|
| `number` / `string` / `choice` / unset | probability, brainteaser, arithmetic, coding | 3 plain-text lines: extracted / reason / YES \| NO | `1.0` if YES else `0.0` |
| `open` | finance | fenced ```json``` block with per-criterion scores | `raw_total / rubric.total_points` (always 0.0–1.0) |

Same judge model (`deepseek/deepseek-v4-pro`) for both paths.
No tools, no web search, no code execution — the model under test runs entirely
on its own knowledge (see [SPEC §4](docs/SPEC_v3.md)).

**Output contract for the model under test.** The system prompt instructs every
model to end its reply with a line of the form `Final Answer: <answer>` (or
`Final Answer: I don't know`). The binary judge looks for this line first and
falls back to scanning the tail of the response only if it's missing. This
eliminates the previous failure mode where the judge couldn't locate the
committed answer in a long reasoning trace.

---

## Dataset

5 files in `data/`, one per category. Pipeline loads them all and concatenates.

| Category | File | Grading | Points each |
|---|---|---|---|
| Probability | `data/probability.json` | binary 0/1 | 1.0 |
| Brain teaser | `data/brainteaser.json` | binary 0/1 | 1.0 |
| Arithmetic | `data/arithmetic.json` | binary 0/1 | 1.0 |
| Coding | `data/coding.json` | binary 0/1 | 1.0 |
| Finance | `data/finance.json` | rubric 1–10 (× 0.1) | 1.0 |

### Question schema (binary)

```json
{
  "id": "b01",
  "topic": "brainteaser",
  "question": "How many trailing zeros in 100! ?",
  "answer": "24",
  "answer_type": "number"
}
```

### Question schema (open / rubric)

```json
{
  "id": "f01",
  "topic": "finance",
  "question": "Compare option prices under different drifts ...",
  "answer_type": "open",
  "answer": {
    "total_points": 10,
    "categories": [
      {
        "name": "Asset Drift Comparison",
        "max_points": 5,
        "criteria": [
          {
            "id": "identical_prices_verdict",
            "name": "Identical Option Prices Verdict",
            "points": 2,
            "description": "Explicitly states that prices do NOT differ ...",
            "trap": "Optional: common error; if model commits this, award 0."
          }
        ]
      }
    ]
  }
}
```

Validator (runs at startup) enforces: required fields, unique IDs across files,
and rubric point totals add up consistently. See [`data/finance.json`](data/finance.json) for a worked example.

**Do not paraphrase or "clarify" a question before adding it.** If a question is
ambiguous, that ambiguity is part of the test. Only fix encoding (`Ã` → `×`)
and JSON-syntax errors.

---

## Output

Every run writes three timestamped files to `results/`:

| File | What's in it | Use it for |
|---|---|---|
| `details_<ts>.json` | One row per `(model, question)`: raw response, extracted answer, judge reasoning, latency, etc. | Auditing the judge, debugging wrong answers |
| `summary_<ts>.json` | Per-model totals + per-category breakdown + `missed_ids` / `error_ids` | Quick leaderboard read |
| `scores_<ts>.csv` | Model × question matrix. Cells are 0/1 (binary) or 0.0–1.0 (rubric) | Open in Excel / pandas |

A live JSONL (`details_<ts>.jsonl`) is also appended as each cell completes —
if the run crashes mid-flight, it's the source of truth.

---

## Repo layout

```
.
├── README.md                <- this file
├── docs/
│   ├── SPEC_v3.md           <- locked experimental spec (read this if writing it up)
│   ├── CHANGELOG.md         <- version history (v3.0 → v3.7)
│   └── archive/             <- older locked specs (v2.0 and earlier)
├── data/                    <- one JSON per category
├── pipeline/
│   ├── models.py            <- model registry (10 models)
│   ├── prompts.py           <- 3 system prompts (edit prompts here, not in code)
│   ├── clients.py           <- OpenRouter client + the two judges
│   ├── dataset.py           <- load + validate questions and rubrics
│   ├── output.py            <- write details / summary / scores files
│   └── runner.py            <- Result schema, evaluate one question, orchestrate
├── run_benchmark.py         <- CLI entry point
├── requirements.txt         <- runtime deps (pinned)
└── results/                 <- benchmark outputs (gitignored)
```

Files you edit during normal iteration:

- `data/<category>.json` — add/remove questions
- `pipeline/models.py` — change the model lineup
- `pipeline/prompts.py` — tweak a prompt

The rest is set-and-forget.

---

## Design choices worth knowing

- **One key, ten models.** All API calls go through OpenRouter so you only manage one credential.
- **Single greedy decode** (`temperature=0`, `top_p=1`). One of the ten models (`gpt-5.5`) silently ignores these params — the Result records `sampling_controlled=false` for those rows so you can qualify the comparison.
- **Lineup mixes frontier + older baselines.** Seven 2026-era models plus three deliberately weaker ones (`gpt-4`, `claude-3-haiku`, `gemini-2.5-flash`) so you can read the SOTA gap directly from the leaderboard.
- **One judge, two prompts.** Binary questions get a 3-line judge prompt; rubric questions get a structured JSON-output judge prompt. Same model handles both.
- **No tools** in the current spec — the model answers on native reasoning alone. The pipeline previously supported calculator + web search (v3.0–v3.5); revert if you want those back.
- **Same prompt across all categories.** Per-category prompt engineering would confound model comparisons. This matches MMLU / GSM8K / MATH methodology.
- **Crash-safe by design.** Every completed cell is appended to a JSONL file under a lock as it finishes — a network drop mid-run does not lose data.
- **Pipeline bugs don't get silently absorbed.** Only API / timeout / judge-parse errors are captured as cell-level `error` (re-runnable). Dataset typos, SDK mismatches, etc. surface as exceptions. **Model truncation** (`finish_reason == "length"`) is counted as a wrong answer, not an error — the model failed to deliver a Final Answer in its 8192-token budget.

---

## Known limitations

These are documented in detail in [SPEC §9](docs/SPEC_v3.md). The headlines:

1. **Sample size is small.** 50 target questions is enough for ranking, not enough for tight statistical claims per category.
2. **Single judge model.** Every grading decision depends on `deepseek-v4-pro`. Audit `judge_reasoning` before publishing any leaderboard.
3. **No tools, no multi-sample.** The benchmark measures one configuration. Real deployments differ.
4. **Training-data leakage risk.** Many classic interview questions appear on prep sites, so models may be remembering rather than reasoning.

---

## Security

- `.env` holds the real API key. It's gitignored. Never commit it.
- `.env.example` is the template that **does** get committed. Keep it empty.
- If a key leaks anywhere (chat, screenshot, accidental push), rotate it at https://openrouter.ai/keys before doing anything else.
