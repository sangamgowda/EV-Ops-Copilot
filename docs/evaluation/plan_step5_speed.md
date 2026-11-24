# Step 5 (speed): rules written before the work

Written 2026-11-24, before any Step 5 change or run. These rules are not changed after a result is seen.

## The problem

On the Groq free tier a model allows about 8,000 tokens per minute. One question can use more than that, so the agent waits for the allowance in the middle of answering. The app gives up after 45 s, so answers that would be correct fail on time. In the held-out check (`result_heldout.md`) the 45 s pass rate was 34.8–41.3% on the golden set against 56.5–65.2% at 120 s, and 5–6 of 8 traps could not be measured because they timed out.

## The question

Does the speed version S answer more questions within the app's 45 s limit than A', without answering worse?

## The base

S starts from A' (commit `c5cedee` + measurement harness, branch `heldout-a-prime`, `10db9c4`), not from X+Y+Z. X+Y+Z was not more accurate (8 fixed, 8 broken) and is slower.

Fix Y1 (weighted baseline) is not carried over: A' computes the baseline as `avg(b.nominal_value)` over raw rows, which is already weighted by the readings. The `max()` bug came with fix X's template, which A' does not have.

## What S may change

Only things that reduce tokens or waiting, for example:

- a shorter planner prompt, with rules moved into code where a plain check does the same job;
- running the database query and the document search at the same time in lap 1;
- sending less evidence text back to the model.

S does not add new answer rules (that is Step 6). Each change is one commit, so it can be tested and undone separately.

## Measurement added first

Tokens per question (input + output of the agent's own calls, the judge excluded) are logged by the harness and shown in each report: mean, median, and the number of questions over 8,000. This is measurement code only, applied to A' and S alike. The A' starting point is measured before any S change.

## Which questions are used for what

- Design and debugging use the 46 golden questions only.
- The 48 held-out questions are not read for design. They are used once, for the final result.

## The runs and pairs, chosen now

| Pair | A' | S |
|---|---|---|
| Golden (46) | run 2, `20261122-214903` | S golden run |
| Held-out (48) | `20261124-124101` | S held-out run |
| Combined | up to 94 cases | |

Same judge (`f66352838f0f`), models, free tier, date pin 2026-10-02, 120 s eval limit, `config/app_config.yaml` (`max_wait_seconds: 40`), exam files unchanged.

## How a case counts

- Pass at 45 s = pass and latency ≤ 45 s (the report's "as the app runs" figure).
- Fixed = fails at 45 s in A', passes at 45 s in S. Broken = the other way round.
- Exact two-sided McNemar on fixed and broken.
- Only cases with `status` ok in both runs count. Traps that time out are not measured.

## The verdict

| Verdict | When |
|---|---|
| **Better** | All four: (1) combined at 45 s: fixed > broken and p < 0.05; (2) held-out alone at 45 s: fixed ≥ broken; (3) the counter-metric holds; (4) mean tokens per question on golden lower than A'. |
| **Faster, not proven** | (1) fixed > broken but p ≥ 0.05, and (3) and (4) hold. |
| **Not better** | Anything else, including any result where (3) fails. |

**Counter-metric (3):** S must not answer worse once time is taken out. On each set, S's pass rate at 120 s is no more than 1 case below A''s (golden: 28 of 46; held-out: 36 of 48). It stops a "win" made by fast but wrong answers.

Reported alongside, not deciding: the 120 s pass rate with 95% interval, the pass rate with budget waits not counted, mean budget wait, tokens per question (mean, median, over 8,000), and traps measured and abstained.

## The result line

> At the app's 45 s limit, pass rate A' a% → S b% on 94 paired questions (N fixed, M broken, McNemar p = …); tokens per question x → y.

If the verdict is not "Better", the line says what was found instead.

## After Step 5

Re-sort the remaining failures (golden only) into groups and choose Step 6 from the biggest group.
