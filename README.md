# Quant Interview Benchmark

A binary-scored benchmark of LLM performance on **quantitative-finance interview questions** with single verifiable answers. Each model gets a score out of 10 (one point per question).

Currently at **v1.0** — proof of concept. See [docs/SPEC_v1.md](docs/SPEC_v1.md) for the locked experimental conditions.

---

## What this measures

A model is given a quant interview question and asked to answer step-by-step. We then:

1. **Model answers** (usually a paragraph of reasoning + a final answer)
2. **An LLM judge** (Claude Sonnet 4.6) extracts the final answer string from the response
3. **A code grader** compares the extracted answer to the ground truth
4. **Score** = number of correct answers out of 10

The benchmark is part of a 4-stage project:
1. Define benchmark (topics / distribution / difficulty)
2. Build dataset ([question, answer] pairs, human-verified) — **upstream of this repo**
3. **Evaluation pipeline ← this repo**
4. Analyze results, detect leakage / poor discrimination, iterate

---

## Pipeline (what actually happens when you run it)

```
data/questions.json (10 quant questions)
        │
        ▼
   for each (model, question):
        │
        ├─► call_model()  ──────────► OpenRouter ─► 10 models (Claude / GPT / Gemini / ...)
        │                                                │
        │                                                ▼
        │                                         raw_response  (paragraph)
        │                                                │
        │                                                ▼
        ├─► judge_extract() ────────► OpenRouter ─► Claude Sonnet 4.6
        │                                                │
        │                                                ▼
        │                                         extracted_answer  ("0.75" or "N/A")
        │                                                │
        │                                                ▼
        └─► grade()  (pure Python, no LLM)
                                                         │
                                                         ▼
                                                    correct: True / False
        │
        ▼
   results/{details,summary,scores}_<timestamp>.{json,csv}
```

Per run: 100 model calls + 100 judge calls = **200 OpenRouter requests**. Cost: ~$0.30–$0.80 depending on which reasoning models think hardest.

---

## Repo file map

Files marked **★** are the ones you'll edit during normal iteration. Everything else is set-and-forget infrastructure.

### Top-level

#### [`README.md`](README.md)
This file. Project overview, file map, output explanation, key v1.0 decisions, how to run, known limitations.

#### **★** [`run_benchmark.py`](run_benchmark.py)
CLI entry point — the **only script you'll ever run directly**. Three jobs:
1. Loads `.env` into the environment via `python-dotenv`
2. Parses CLI flags (`--questions` / `--out` / `--concurrency`)
3. Calls `pipeline.runner.run_benchmark()` and prints a leaderboard

#### [`requirements.txt`](requirements.txt)
Python dependencies, **versions pinned** (`openai==1.59.9`, `python-dotenv==1.0.1`). Pinning is intentional for reproducibility — a benchmark re-run six months from now shouldn't behave differently because the SDK changed under us. Bump versions deliberately and record it as a v1.x bump.

#### [`.env.example`](.env.example)
Environment-variable **template**. Lists the required keys (`OPENROUTER_API_KEY`, optional `OPENROUTER_REFERER`, `OPENROUTER_TITLE`) with **empty values**. Committed to git. **Never put real secrets here.**

#### `.env` (gitignored) ⚠️
Where your **real** `OPENROUTER_API_KEY` lives. Excluded by `.gitignore`. If you ever paste this key publicly (chat, screenshot, accidental commit), rotate it at https://openrouter.ai/keys.

#### [`.gitignore`](.gitignore)
Excludes from version control: `.env`, all common venv directory names (including `LLM/`), `__pycache__/`, `results/` (every run regenerates), IDE configs, `.DS_Store`, `*.key`.

---

### `data/` — dataset

#### **★** [`data/questions.json`](data/questions.json)
The 10 questions. **Swap this file when you have real interview questions** — nothing else in the pipeline needs to change.

Schema per entry:
```json
{
  "id": "q01",                  // unique; becomes column name in scores CSV
  "question": "...",            // sent verbatim to the model
  "answer": "6",                // ground truth
  "answer_type": "number",      // "number" | "string" | "choice"
  "tolerance": 0.001,           // optional; absolute tolerance for numerics
  "topic": "probability",       // optional metadata for v2.0 analysis
  "difficulty": "easy"          // optional
}
```

`run_benchmark.py` validates this file at startup: missing required fields, duplicate IDs, and invalid `answer_type` cause an immediate exit before any API calls are made.

---

### `pipeline/` — core code

#### [`pipeline/__init__.py`](pipeline/__init__.py)
Empty file. Its presence tells Python that `pipeline/` is a package, so `from pipeline.runner import run_benchmark` works. **Should stay empty.**

#### **★** [`pipeline/models.py`](pipeline/models.py)
The model registry. A `MODELS` list of `ModelConfig` entries, each with three fields:
- `name` — display label (appears in CSV/JSON output)
- `model_id` — OpenRouter's identifier (verified against `/api/v1/models`)
- `supports_temperature` — `False` for the 3 reasoning models that ignore sampling params

**Edit this file to add, remove, or swap models.** No other code changes needed.

#### [`pipeline/clients.py`](pipeline/clients.py)
All OpenRouter network code. Two public async functions:

- `call_model(cfg, prompt) → str` — sends the question to a model under test, returns its raw response
- `judge_extract(raw_response) → str` — sends the raw response to **Claude Sonnet 4.6** (the judge), returns a clean extracted answer or `"N/A"`

Also defines three constants worth knowing:
- `SYSTEM_PROMPT` — the instructions sent to every model under test
- `JUDGE_SYSTEM_PROMPT` — the (Chinese) prompt that tells the judge to extract only the answer
- `JUDGE_MODEL` — `"anthropic/claude-sonnet-4.6"`; change one line to swap judges

A lazy-singleton `AsyncOpenAI` client is used so we open one HTTP connection pool for all 200 calls per run.

#### [`pipeline/evaluator.py`](pipeline/evaluator.py)
The grader — **pure code, no LLM**. Public function `grade(extracted, expected, answer_type, tolerance) → bool` dispatches to one of three rules:
- `_grade_number` — parses both sides (handles plain decimals, fractions like `2/3`, percentages like `16.67%`, LaTeX `\frac{a}{b}`), absolute-tolerance comparison
- `_grade_string` — word-boundary, case-insensitive (`"yes"` matches inside `"Yes, they are independent."`)
- `_grade_choice` — first A–E letter in the extracted answer matches expected

Because grading is deterministic code, the same `(extracted, expected)` pair always produces the same verdict — useful for blaming bugs (judge issue vs grader issue).

#### [`pipeline/runner.py`](pipeline/runner.py)
Orchestration. Defines:
- `Result` dataclass — the fields logged per `(model, question)` call (see below)
- `_validate(questions)` — startup sanity check on `data/questions.json`
- `_eval_one(cfg, q, sem)` — the per-pair flow: `call_model → judge_extract → grade`, wrapped in try/except so any single failure becomes an `error` row instead of crashing the whole run
- `run_benchmark(...)` — fans out all `model × question` tasks with `asyncio.Semaphore(concurrency=8)` for rate-limiting
- `_write_outputs(...)` — produces the three output files

---

### `docs/` — specification

#### [`docs/SPEC_v1.md`](docs/SPEC_v1.md)
The **locked v1.0 specification** — 10 sections covering exactly which models, prompt, sampling params, tool permissions, dataset format, judge model, scoring rules, evaluation protocol, known limitations, and versioning rules apply. This is what you'd quote in a methods section of a writeup, and what future you needs to read before changing anything that would break comparability.

---

### Runtime artifacts (gitignored)

#### `LLM/` — Python virtual environment
Created with `python3 -m venv LLM`. ~200 MB after `pip install -r requirements.txt`. Tied to this machine's Python — recreate on another machine, don't commit. The `★` files run with `LLM/bin/python` (or after `source LLM/bin/activate`).

#### `results/` — benchmark outputs
Three timestamped files per run. Accumulates over time so you can compare runs (before/after a prompt change, etc.). Prune manually when it gets cluttered.

---

## Output files (what the 3 results files actually contain)

Each run of `python run_benchmark.py` produces three files in `results/`, all sharing a timestamp `YYYYMMDD-HHMMSS`. They contain **the same evaluation results** in three formats optimized for different uses.

### `details_<ts>.json` — the raw record (debugging / auditing)

A JSON array of 100 entries — one per `(model, question)` pair. **This is where the truth lives**; the other two files are derived from it.

Each entry includes everything the pipeline saw and decided:

```json
{
  "model": "claude-opus-4.7-fast",
  "question_id": "q01",
  "correct": true,
  "extracted_answer": "6",                  // what the judge pulled out
  "expected_answer": "6",                   // ground truth from data/questions.json
  "raw_response": "Final answer: 6",        // unmodified model output
  "latency_s": 2.55,
  "sampling_controlled": false,             // did our temperature=0 actually apply?
  "error": null
}
```

Use this file when you want to **audit the judge** (compare `raw_response` to `extracted_answer`), **debug wrong answers** (was it the model, the judge, or the grader?), or measure latency. Typical size: 50–200 KB depending on how chatty the reasoning models were.

### `summary_<ts>.json` — per-model totals (quick read)

One block per model. No per-question detail. Use this for a fast "who scored what" answer:

```json
{
  "timestamp": "20260518-141030",
  "num_questions": 10,
  "num_models": 10,
  "per_model": {
    "claude-opus-4.7-fast": {
      "correct": 10, "total": 10, "errors": 0,
      "sampling_controlled": false,
      "score": 10, "accuracy": 1.0
    },
    ...
  },
  "details_file": "details_20260518-141030.json"
}
```

`score` is correct count (1 point per question, as designed). `accuracy` is the fraction. `errors` is API/judge failures (graded as wrong, but distinguishable). `sampling_controlled=false` flags the 3 reasoning models that ignore our `temperature=0`.

### `scores_<ts>.csv` — the model × question matrix (analysis)

A spreadsheet. Rows = models (sorted by total score descending). Columns = each question (0 or 1) + total + accuracy + sampling flag:

```
model,q01,q02,q03,q04,q05,q06,q07,q08,q09,q10,score,accuracy,sampling_controlled
claude-opus-4.7-fast,1,1,1,1,1,1,1,1,1,1,10,1.000,no
claude-sonnet-4.6,1,1,1,1,1,1,1,1,1,1,10,1.000,yes
gpt-5.5,1,1,1,1,1,1,1,1,1,1,10,1.000,no
...
```

Open in Excel, Numbers, or pandas. The matrix view makes it obvious which questions discriminate (a column with mixed 0/1) versus which are trivially easy (all 1) or impossibly hard (all 0).

### Why timestamped and not overwriting

Each run is an experiment. You'll want to compare — same dataset, different prompt; same prompt, different models; same setup, different days. Cleanup is your job:

```bash
ls -t results/details_*.json | tail -n +4 | xargs rm   # keep the 3 most recent runs
rm results/*.{json,csv}                                # nuke everything
```

---

## Key v1.0 decisions

**Models**: 10, all routed through **OpenRouter** (one API key reaches everyone). Versions are pinned, no `~latest` aliases — runs months apart should be comparable.

**Routing — why OpenRouter, not direct APIs**:
- One key vs 8 separate provider accounts
- New models on OpenRouter the day they launch
- Cost: ~5% markup, irrelevant at our volume
- Trade-off: can't use provider-exclusive features (Claude extended-thinking mode, OpenAI logprobs, etc.)

**Three models silently ignore our sampling settings** — surfaced in results via `sampling_controlled=false`:
- `anthropic/claude-opus-4.7-fast`
- `openai/gpt-5.5`
- `openai/gpt-chat-latest`

These reasoning models don't expose `temperature` / `top_p` controls; OpenRouter drops those params. The other 7 honor them.

**Prompt**: Zero-shot. Brief system prompt asks for chain-of-thought reasoning + `Final answer: X` on the last line. No few-shot examples (avoids contaminating answer style).

**Sampling**: `temperature=0`, `top_p=1.0`, `max_tokens=8192`. Greedy decoding for reproducibility.

**Tools all DISABLED in v1.0**: no web search, no code execution, no calculator, no RAG, no function calling. v1.0 measures native reasoning; tool variants are deferred so they can A/B against this baseline.

**Answer extraction**: LLM judge (Claude Sonnet 4.6), not regex. The judge sees only the raw response — **not the question, not the expected answer** — so it cannot bias toward correctness. Returns a clean answer string or `"N/A"`.

**Grading rules**:
- `number`: absolute tolerance, default `1e-4`, overridable per question (q03's `0.1667` uses `0.001`)
- `string`: word-boundary, case-insensitive (`"yes"` matches inside `"Yes, they are independent."`)
- `choice`: first letter A–E

**Not in v1.0** (deferred):
- Retry on transient errors
- Token / cost / reasoning-token logging
- Majority vote / multi-sample
- LLM-as-judge for grading (LLM is used only for extraction; correct/wrong is pure code)
- Mandatory human review pass before publishing a leaderboard

---

## How to run

```bash
# One-time setup
python3 -m venv LLM                          # create ./LLM/ venv
LLM/bin/pip install -r requirements.txt
cp .env.example .env                         # then fill OPENROUTER_API_KEY in .env

# Run
source LLM/bin/activate                      # or use LLM/bin/python directly
python run_benchmark.py                      # ~30s–2min, $0.30–$0.80
deactivate

# Inspect
cat results/scores_*.csv                     # quick view
python -m json.tool results/summary_*.json   # per-model totals
```

CLI flags:
```
--questions PATH    use a different question file (default: data/questions.json)
--out DIR           write outputs to a different directory (default: results/)
--concurrency N     max in-flight API calls (default: 8)
```

---

## Known v1.0 limitations

1. **Sample size 10** — not statistically powerful. v2.0 will expand to 50–100.
2. **No retry on transient errors** — a single API failure leaves that cell as `error`.
3. **Training-data contamination risk** — classic interview problems may be memorized.
4. **3 of 10 models can't have controlled sampling** — qualified via `sampling_controlled` flag.
5. **Judge is itself an LLM** — could occasionally misextract; manual audit of edge cases is required before publishing.
6. **No tool use** — doesn't reflect realistic LLM usage today; deferred to v1.1+.

See [docs/SPEC_v1.md § 9](docs/SPEC_v1.md) for the full list.

---

## Security note

**Never put a real API key in `.env.example`** — it's committed to git. Real secrets go in `.env`, which is gitignored. If you accidentally commit (or paste) a real key anywhere public, rotate it immediately at https://openrouter.ai/keys.
