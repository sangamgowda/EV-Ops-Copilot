# Held-out check: rules written before the test

Written 2026-11-24, before any held-out run. These rules are not changed after a result is seen.

## The question

Is X+Y+Z (fixes X, Y and Z, branch `eval-a-prime`) better than A' (commit `c5cedee`), on questions that were not used to build the fixes?

## The runs

| # | Run | Code | Case set |
|---|---|---|---|
| 1 | A' held-out | `c5cedee` + the measurement harness from `160ac6c` only (branch `heldout-a-prime`) | 48 held-out |
| 2 | X+Y+Z held-out | `eval-a-prime` at the commit that adds this file | 48 held-out |
| 3 | X+Y+Z golden, second run | same as run 2 | 46 golden + 8 traps |

Already done and used as they are:

- A' golden run 1: `20261121-120508`
- A' golden run 2: `20261122-214903`
- X+Y+Z golden run 1: `20261123-145910`

## Kept the same in every run

- Exam files are not edited. `heldout.jsonl` = git blob `1eee1a51`, `golden.jsonl` = git blob `4355e86a`.
- Judge prompt unchanged (judge version `f66352838f0f`). The judge was checked against 30 hand labels: pass/fail agreement 90%, kappa 0.795.
- Same models (gpt-oss-20b, gpt-oss-120b, judge qwen3.8-27b), Groq free tier, date pinned to 2026-10-02, 120 s limit per question.
- Same `config/app_config.yaml` for both versions, including `llm.queue.max_wait_seconds`.
- No change to prompts or code on either branch until all three runs are finished. Resuming after a daily limit uses `--resume <run id>`, which keeps the same commit.

## How a case counts

- A case counts only if it finished with a score (`status` ok) in both runs being compared. Infrastructure errors and unfinished cases are left out on both sides.
- A timeout is a fail, as in every earlier run. Traps that time out are not measured.
- Fixed = failed in A', passes in X+Y+Z. Broken = passed in A', fails in X+Y+Z.
- The p value is the exact two-sided McNemar test on fixed and broken (`scripts/compare_runs.py`).

## The pairs, chosen now

- Held-out: run 1 against run 2 (48 cases).
- Golden: A' run 2 (`20261122-214903`) against X+Y+Z golden run 2 (run 3 above). A' run 2 is the better A' run (60.9%), so this pairing does not favour the fixes, and the X+Y+Z run it is paired with has not happened yet.
- Combined: held-out pair + golden pair = up to 94 cases.

## The verdict

| Verdict | When |
|---|---|
| **Better** | Combined: fixed > broken and p < 0.05. Held-out alone: fixed > broken. Traps: X+Y+Z abstains on at least as many measured traps as A' run 2, minus 1. |
| **Promising, not proven** | Combined: fixed > broken, but p ≥ 0.05. |
| **Not better** | Combined: fixed ≤ broken, or held-out alone: fixed < broken. |

Reported alongside the verdict but not deciding it: pass rate with 95% interval for each run, the pass rate at the app's 45 s limit, the pass rate with budget waits not counted, and the golden run 1 pair (A' run 1 against X+Y+Z run 1) as a check on direction.

## The result line

Whatever the verdict, the line is built from these numbers:

> Held-out pass rate A' a% → X+Y+Z b% (48 unseen questions); on 94 paired questions N fixed, M broken (McNemar p = …).

If the verdict is not "Better", the line says so, for example "no significant gain on unseen questions".

## Known limits

- All 48 held-out questions are `synthetic_data` (answered from the database). They test fixes X and Y (SQL, baseline, currency) more than fix Z (document retrieval). The golden pair covers documents.
- One held-out run per version. The free tier allows no more in the time available.
