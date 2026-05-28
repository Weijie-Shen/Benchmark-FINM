# Quant Interview Benchmark — Report

A small, opinionated benchmark of how well frontier LLMs solve
quantitative-interview questions across five categories: **probability,
brain teasers, machine learning, corporate finance, and (financial)
derivatives**.

This report covers the **run3** dataset:
- 10 models × 50 questions = **500 cells**
- Cross-judge validated under **5 different judge models** (deepseek,
  gemini, grok, sonnet, qwen)
- Default judge: `deepseek/deepseek-v4-pro` — chosen for accuracy and
  rigor (see [§ "Why deepseek as canonical"](#why-deepseek-as-canonical) below)

For the locked experimental conditions see [docs/SPEC_v3.md](docs/SPEC_v3.md).
For version history see [docs/CHANGELOG.md](docs/CHANGELOG.md).

---

## 1. Methodology in one paragraph

Each model is asked all 50 questions through OpenRouter with a single
neutral system prompt (`temperature=0`, `top_p=1`, no tools, no
multi-sample). The model's reply must end with a `Final Answer:` line.
A second LLM call grades the response against the expected answer:
binary 0/1 for 40 of the questions (probability / brainteaser / ML /
corporate-finance), and a structured rubric (per-criterion 0–N) for the
10 derivatives questions, normalized to [0, 1]. The score is the sum
across questions — max 50.0.

To check that the leaderboard isn't an artifact of one judge's
idiosyncrasies, the same 500 model outputs were re-judged under 4
additional judges (gemini-3.1-flash-lite, x-ai/grok-4.3,
claude-sonnet-4.6, qwen3.6-flash) using the same code path. The
canonical leaderboard below uses deepseek; cross-judge agreement is
shown in [§4](#4-cross-judge-validation).

Robustness machinery that materially affected the numbers:
- A **deterministic numeric pre-check** auto-passes ~25 % of binary
  cells where the model's committed answer and the expected value are
  the same number to within 0.01 % relative tolerance — no judge call,
  no judge variance.
- A **tolerant rubric-JSON parser** absorbs the common LLM output
  quirks (trailing commas, whitespace-padded keys, prose preambles,
  stray non-dict items in the scores list).
- A **bottom-up YES/NO scanner** correctly reads judge verdicts when
  prefixed (`Line 3: YES`, `**YES**`, `Verdict: NO.`) — the previous
  `startswith("YES")` check had silently mis-scored ~3 % of cells.

---

## 2. Headline result — model leaderboard

Per-model accuracy under the default judge (deepseek-v4-pro):

![Leaderboard — deepseek canonical](report_figures/leaderboard.png)

| Rank | Model | Score | Accuracy |
|---:|---|---:|---:|
| 1 | gpt-5.5 | 45.25 / 50 | **90.5 %** |
| 2 | seed-2.0-lite | 40.95 / 50 | 81.9 % |
| 3 | qwen3.6-flash | 39.85 / 50 | 79.7 % |
| 4 | grok-4.3 | 38.55 / 50 | 77.1 % |
| 5 | claude-sonnet-4.6 | 38.45 / 50 | 76.9 % |
| 6 | deepseek-v4-flash | 38.20 / 50 | 76.4 % |
| 7 | gemini-3.1-flash-lite | 35.55 / 50 | 71.1 % |
| 8 | gemini-2.5-flash | 30.80 / 50 | 61.6 % |
| 9 | gpt-4o | 24.70 / 50 | 49.4 % |
| 10 | claude-3-haiku | 13.25 / 50 | 26.5 % |

Three observations are immediate:

1. **gpt-5.5 wins decisively** at 90.5 % — a 9-point gap to second
   place. The next six models cluster within 6 points of each other
   (76.4 – 81.9 %). After that there are visible breaks: a ~9-point
   drop to gemini-2.5-flash, ~12 to gpt-4o, ~23 to claude-3-haiku.
2. **The frontier tier is a tight band, not a hierarchy.** seed,
   qwen, grok, claude-sonnet, deepseek-flash all sit between 76 % and
   82 %. Re-ordering depends on the judge.
3. **The two-tier "frontier + older baselines" design held up.**
   gpt-4o (49 %), claude-3-haiku (26 %), and to a lesser extent
   gemini-2.5-flash (62 %) all sit well below the frontier cluster,
   confirming the spread the benchmark was designed to measure.

---

## 3. Per-category breakdown

![Per-category accuracy heatmap (deepseek)](report_figures/heatmap_categories.png)

Where each model lands per category, scored by deepseek:

| Category | Total awarded / 100 | Hardest cells |
|---|---:|---|
| corporate_finance | 80.0 | cf07 (only seed-2.0-lite scored it; everyone else missed it under deepseek) |
| machine_learning | 74.0 | ml06 (expected vs body-extracted disagreement), ml03 (math) |
| probability | 69.0 | p03, p06, p07, p09 — hard combinatorics; even strong models often skipped a `Final Answer:` line |
| brainteaser | 67.0 | b05, b07 — large-integer / cycle problems |
| **derivatives** | **55.55** | rubric-graded; structural ceiling around 60 even for the strongest model |

The pattern of model strength varies by category:

- **gpt-5.5 sweeps**: 100 % on probability and ML, 90 % on brainteaser
  and corporate finance, 72 % on derivatives. Only derivatives keeps it
  below ceiling.
- **claude-sonnet-4.6**: 100 % corporate finance but only 50 %
  brainteaser. The brain teasers in this benchmark are unusually large
  combinatorial puzzles (b07: `3^30 + 40` crates with a 3-cycle
  reduction) — claude-sonnet seems to disengage from "look up the
  pattern" reasoning where qwen, gpt-5.5, grok all just commit and get
  it right.
- **gpt-4o**: only 10 % on probability — the older models are visibly
  weaker on raw combinatorial work, despite holding their own on
  corporate finance (80 %).
- **claude-3-haiku** is uniformly weak (15–50 % per category) — this is
  exactly the kind of behavior the older-baseline tier was included to
  show.

**Derivatives is the structural hard category for every model.** Even
the top model scores 7.3 / 10. The next-tier models (qwen, grok,
sonnet, deepseek-flash) cluster around 5.2–5.5 / 10. This is partly
genuine difficulty — these are open-ended quantitative-finance
questions graded against multi-criterion rubrics — and partly
judgement noise, addressed in [§4.3](#43-rubric-judging-is-the-real-noise-floor) below.

---

## 4. Cross-judge validation

The leaderboard above uses deepseek as the single judge. To check that
deepseek isn't an outlier, the same 500 model outputs were re-judged
by 4 other models. Key question: **does the ranking depend on the
judge?**

### 4.1 Overall agreement: 93.6 % of binary cells

![5-judge verdict agreement + split patterns](report_figures/agreement_breakdown.png)

Of 400 binary-graded cells (the 40 non-derivatives questions × 10
models):

| Pattern | Count | % |
|---|---:|---:|
| All 5 judges say YES | 283 | 70.8 % |
| All 5 judges say NO | 91 | 22.8 % |
| 4-vs-1 (one dissenter) | 13 | 3.2 % |
| 3-vs-2 (genuine split) | 13 | 3.2 % |

**93.6 % of binary verdicts are unanimous across 5 judges of different
lineages.** 

The split breakdown is informative: the most common pattern is
`gemini=YES, deepseek=NO, grok=NO, sonnet=YES, qwen=NO` — i.e. the
lenient judges (gemini, sonnet) saying YES where the strict trio
(deepseek, grok, qwen) says NO. Sonnet stands alone as the most
generous judge; grok stands alone as the most strict. The other 3
judges sit in between.

### 4.2 Per-category cross-judge variance

![Per-category spread across 5 judges](report_figures/category_spread.png)

| Category | Spread (max − min, across 5 judges) | Interpretation |
|---|---:|---|
| corporate_finance | 4 | Tight — single-token answers, no ambiguity |
| machine_learning | 5 | Tight after the ml07 fix (was 13 in run2) |
| probability | 6 | Tight |
| brainteaser | 7 | Tight after the dataset rewrite (was 11 in run2) |
| **derivatives** | **16.35** | **Wide — see [§4.3](#43-rubric-judging-is-the-real-noise-floor)** |

The four binary categories agree across judges to within 4–7 points
(out of 100 total points per category — i.e. 4–7 %). Within the noise
this benchmark can resolve, the leaderboard ordering for those
categories is judge-independent.

Derivatives is a different story.

### 4.3 Rubric judging is the real noise floor

![Top-30 rubric cells by cross-judge spread](report_figures/rubric_spread.png)

The 100 derivatives cells (10 questions × 10 models) show much wider
per-cell variance than the binary path. 27 of those 100 cells have a
spread of more than 2 points (out of 10) across the 5 judges. The
worst cases hit 8 points:

| Cell | gemini | deepseek | grok | sonnet | qwen | spread |
|---|---:|---:|---:|---:|---:|---:|
| seed-2.0-lite / d03 | 2.0 | 2.0 | 2.0 | **10.0** | 2.0 | 8.0 |
| claude-sonnet-4.6 / d03 | 2.0 | 2.0 | 2.0 | **10.0** | 2.0 | 8.0 |
| gemini-3.1-flash-lite / d03 | 2.0 | 2.0 | 2.0 | **10.0** | 2.0 | 8.0 |
| gemini-3.1-flash-lite / d09 | 8.5 | 3.0 | 2.0 | 7.0 | 5.5 | 6.5 |

The d03 pattern is striking: **sonnet gives a 10/10 perfect score to 3
different models that all 4 other judges score at 2/10**. This is the
clearest single-judge anomaly in the dataset. It's not random — it's
systematic on a specific rubric, suggesting either (a) the d03 rubric
underspecifies something sonnet weights differently, or (b) sonnet has
a quirk on that particular criterion set. This is exactly the kind of
finding multi-judge validation is supposed to surface.

**Implication**: any per-cell or per-question claim about derivatives
performance carries meaningful judge-choice dependency. The benchmark
should be read at the category-aggregate level or with multi-judge
consensus, not single-judge per-cell.

### Why deepseek as canonical

Across the 5 judges:

| Judge | Total / 500 | Avg accuracy |
|---|---:|---:|
| sonnet | 372.25 | 74.5 % |
| gemini | 358.70 | 71.7 % |
| qwen | 355.00 | 71.0 % |
| **deepseek** | **345.55** | **69.1 %** |
| grok | 338.90 | 67.8 % |

Deepseek sits in the middle (median position), one tier above
the strictest (grok) and below the lenient pair (gemini, sonnet). It's
a **reasoning model** — its hidden chain-of-thought handles the
"actively verify numeric equivalence" rule in the judge prompt
correctly, where the non-reasoning judges (gemini-flash-lite) often
fall back to surface-string comparison.

It's also the most **rigorous**: when deepseek says YES, all 5 judges
say YES 95+ % of the time. When deepseek says NO, the lenient judges
sometimes say YES — but the model output is reliably borderline. The
"all 5 say YES" set under deepseek is a cleaner positive set than under
sonnet or gemini.

The cost is low ($0.54 per judge pass) and the verdicts are
deterministic (`temperature=0` is honored). For a published
single-judge leaderboard, deepseek is the conservative choice.

---

## 5. Main conclusions

1. **gpt-5.5 is the SOTA model for quant-interview reasoning** at the
   time of run3 (May 2026). Its 90.5 % accuracy is a 9-point lead over
   the next-best model, and it wins under every one of the 5 judges
   tested. Outside derivatives, it scores 90–100 % across every
   category.

2. **The frontier-vs-baseline gap is real and visible.** gpt-4o (49 %)
   and claude-3-haiku (26 %) score well below the 76–82 % frontier
   cluster, confirming the gap the benchmark is designed to measure.
   The two-tier design was the right call.

3. **Binary judgement is well-tamed.** With the v3.12 pipeline
   robustness work, 93.6 % of binary verdicts are unanimous across 5
   judges of different lineages. The remaining 6.4 % split rate is
   small enough that the binary leaderboard ordering is essentially
   judge-independent.

4. **Rubric judgement remains the noise floor.** The derivatives
   category shows a 16-point cross-judge spread (out of 100 max points
   per category) on the same model outputs. Single-judge rubric scores
   on individual cells should be treated as point estimates ± a real
   error bar.

5. **Multi-judge validation works as designed.** Running the same data
   through 5 judges takes ~$5 (model calls done once) + ~$7 (5 judge
   passes) total, exposes per-judge biases (the d03 sonnet anomaly was
   not detectable from a single-judge run), and gives the canonical
   judge's leaderboard a clear "what would change under different
   evaluation" annotation.

---

## 6. Evaluation: What this benchmark cannot measure — the blind spots

What the leaderboard above does and doesn't generalize to:

| # | Blind spot | What's missing |
|---|---|---|
| 1 | **Sample size** | 50 questions, 10 per category. Enough to rank models, not enough to claim a 2-point gap is statistically meaningful. Per-category bars should be read with this in mind — flipping any 2-3 cells changes the model order. |
| 2 | **Single greedy decode** | `temperature=0`, single sample. Doesn't measure capability *range* — some models benefit enormously from multi-sample voting or chain-of-thought sampling, others not. This benchmark says nothing about that. |
| 3 | **No tools** | Pure native reasoning. Real-world quant code uses calculators, references, code execution. A model that scores 49 % here (gpt-4o) may be perfectly usable in production with a Python REPL attached. **The leaderboard is NOT predictive of agent / tool-using performance.** |
| 4 | **Training-data leakage** | Many classic interview questions ("100 prisoners and hats", "egg-drop", coupon-collector variants) appear on prep websites. Models may be *remembering* rather than *reasoning*. The benchmark has no defense — same fundamental issue as MMLU. |
| 5 | **Ceiling effect** | gpt-5.5 at 90.5 % already approaches the headroom of this dataset. A stronger model would mostly differ on derivatives (where the judge introduces noise) and on a handful of binary cells where the dataset itself is borderline. The benchmark has roughly one generation of frontier-model improvement left before it stops differentiating. |
| 6 | **Rubric judging is the noise floor** | 16-point cross-judge spread on derivatives (see §4.3). Single-judge per-cell derivatives claims should be treated as point estimates ± a real error bar. For paper-worthy derivatives claims, multi-judge consensus or human grading is required. |
| 7 | **No prompt-sensitivity measurement** | One neutral system prompt. Some models are far more prompt-sensitive than others; their scores here reflect the prompt as much as their reasoning. Changing the prompt and re-running would give an upper bound on this — not done here. |
---

The data underlying this report — full per-cell judge transcripts —
lives in [results/details_run3_{deepseek,gemini,grok,sonnet,qwen}.json](results/).
Figures are in [report_figures/](report_figures/); regenerate any time
with `LLM/bin/python make_report.py --canonical-judge deepseek`.
