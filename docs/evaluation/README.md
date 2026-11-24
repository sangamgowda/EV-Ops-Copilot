# Evaluation records

How the assistant's answer quality was measured, what was changed to improve it, and what the measurements showed. Every result here comes from a recorded run. The rules for judging a change were written down before the runs that tested it.

## In short

| Version | What changed | Pass rate, 46 practice questions | Pass rate, 48 unseen questions |
|---|---|---|---|
| **A'** (baseline) | nothing | 56.5% and 60.9% (two runs) | 75.0% |
| B | X: SQL-writing rules in the planner prompt | 52.2% | not run |
| X+Y+Z | X, plus Y: correct baseline and rupees, plus Z: retrieval query names the cause | 65.2% and 60.9% (two runs) | 75.0% |

**Conclusion so far:** X+Y+Z is not more accurate than A'. On 94 paired questions it fixed 8 answers and broke 8 (McNemar p = 1). The fixes worked on their own targets (rejected SQL queries fell from about 21 to 4 per run), but other answers broke. X+Y+Z is also slower: its longer prompt waits more for the free tier's token allowance, so at the app's 45 s limit it passes fewer questions (34.8% against 39–41%). The next step is making a question fit in that allowance ([plan_step5_speed.md](plan_step5_speed.md)).

## How a question is scored

- **Code checks**: right vehicle, right tools, numbers within tolerance, real citations, no forbidden claims.
- **AI judge** (a different model family, `qwen3.8-27b`) scores completeness, correctness and hedging from 1 to 4 against the reference answer.
- **Pass** = all code checks pass and every judge score is 3 or more.
- **The judge was checked** against 30 answers labelled by hand. They agreed on pass or fail 90% of the time (κ = 0.795; 0.6 or more is acceptable).
- **Trap questions** (8) have no answer in the data. The right answer is to say so. They are scored separately.
- **Unseen questions**: a held-out set of 48 questions that were not used while designing fixes.

## How results are compared

- **95% intervals.** With 46 questions, a pass rate moves by about ±14 points between runs by chance alone.
- **Paired comparison.** The same question is compared across two versions. Fixed means it failed before and passes now; broken means the reverse.
- **McNemar test.** Its p value is the chance of a fixed/broken split this lopsided if nothing really changed. Below 0.05 counts as a real change.
- **Same conditions.** Exam files, judge, models, date pin (2026-10-02), time limit (120 s in evaluation, 45 s in the app) and configuration are identical across runs.
- **Rules first.** What counts as "better" is committed before the runs that decide it.

## The records, in order

| # | Record | What it is |
|---|---|---|
| 1 | [baseline_A.md](baseline_A.md) | First baseline (56.5%) and the date problem found in the exam |
| 2 | [plan_A_to_B.md](plan_A_to_B.md) | The plan: fair baseline A', one fix, measure B |
| 3 | [baseline_A_prime.md](baseline_A_prime.md) | A', the fair baseline: two runs, 56.5% and 60.9% |
| 4 | [fix_x_check_vs_A_prime.md](fix_x_check_vs_A_prime.md) | Fix X on 18 targeted questions |
| 5 | [result_B.md](result_B.md) | B = A' + X, full run: 52.2% |
| 6 | [fix_y_check_vs_A_prime_and_B.md](fix_y_check_vs_A_prime_and_B.md) | Fix Y check and failure analysis: retrieval wording is the bottleneck |
| 7 | [fix_z_check_vs_Y_A_prime_B.md](fix_z_check_vs_Y_A_prime_B.md) | Fix Z check: 9 of 18, up from 6 |
| 8 | [result_XYZ.md](result_XYZ.md) | X+Y+Z, full run 1: 65.2% |
| 9 | [plan_heldout.md](plan_heldout.md) | Rules for the unseen-question check, written before it |
| 10 | [result_heldout.md](result_heldout.md) | Unseen questions and X+Y+Z run 2: not better, and slower |
| 11 | [plan_step5_speed.md](plan_step5_speed.md) | Rules for the next step, speed |

[logs/](logs/) has the question-by-question log of every run.

## Run it yourself

```bash
python scripts/run_eval.py                    # 46 practice + 8 trap questions
python scripts/run_eval.py --heldout          # 48 unseen questions
python scripts/run_eval.py --resume <run id>  # continue after the free tier's daily limit
python scripts/run_eval.py --report <run id>  # rebuild a run's report
python scripts/compare_runs.py <before> <after>   # fixed, broken, McNemar p
python scripts/label_eval.py                  # label answers by hand
python scripts/run_eval.py --agreement        # judge against the hand labels
```

Runs are written to `evaluation_runs/<run id>/`, which is not committed. The question sets and their format are described in [src/ops_copilot/evaluation/golden/README.md](../../src/ops_copilot/evaluation/golden/README.md).
