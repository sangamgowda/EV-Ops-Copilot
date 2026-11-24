# Held-out check: A' against X+Y+Z

Measured 2026-11-24, under the rules in `plan_heldout.md` (committed ff95a0f before any of these runs).

**Verdict: Not better.** On 94 paired questions X+Y+Z fixed 8 answers and broke 8 (exact McNemar p = 1). On the 48 unseen questions the pass rate is the same, 75.0% → 75.0%. X+Y+Z is also slower: at the app's 45 s limit it is lower than A' in every run.

## The runs

| Run | Code | Case set | Result |
|---|---|---|---|
| `20261124-124101` | A' = `c5cedee` + harness `160ac6c` (branch `heldout-a-prime`, `10db9c4`) | 48 held-out | 36/48 = 75.0% (61.2–85.1) |
| `20261124-161852` | X+Y+Z, `ff95a0f` | 48 held-out | 36/48 = 75.0% (61.2–85.1) |
| `20261124-181246` | X+Y+Z, `ff95a0f` | 46 golden + 8 traps | 28/46 = 60.9% (46.5–73.6) |

Earlier runs used as they were: A' golden run 1 `20261121-120508` (56.5%), A' golden run 2 `20261122-214903` (60.9%), X+Y+Z golden run 1 `20261123-145910` (65.2%).

One case was re-run: in the A' held-out run, `ho_odometer_V-008` hit a network outage (DNS failure, 2290.7 s). It was re-run alone on the same commit (`20261124-154111`) and that result replaces the first. It fails both times; the re-run answer says 11,640 km against 11,644 km ±1. Same judge (`f66352838f0f`), models, date pin, 120 s limit and config (`max_wait_seconds: 40`) in every run.

## The pairs chosen in advance

| Pair | A' | X+Y+Z | Fixed | Broken | McNemar p |
|---|---|---|---|---|---|
| Held-out (48) | 75.0% | 75.0% | 2 | 2 | 1.0 |
| Golden, A' run 2 against X+Y+Z run 2 (46) | 60.9% | 60.9% | 6 | 6 | 1.0 |
| **Combined (94)** | | | **8** | **8** | **1.0** |
| Direction check: golden run 1 against run 1 (46) | 56.5% | 65.2% | 8 | 4 | 0.39 |

Checked against the rules:

- Combined fixed > broken: **no** (8 = 8) → Not better.
- Held-out alone fixed > broken: no (2 = 2).
- Traps: X+Y+Z run 2 abstained on 2 of 2 measured traps, A' run 2 on 3 of 3 (rule: at least 3 − 1 = 2). Met, but on very few traps: 6 of 8 X+Y+Z traps and 5 of 8 A' traps were not measured, because they timed out (X+Y+Z: about 50 s of budget waiting each). The trap comparison is not meaningful until answers fit the time limit.

Mean of two golden runs: A' 58.7%, X+Y+Z 63.0%. The earlier "58.7% → 65.2%" compared one X+Y+Z run with the A' mean; the second X+Y+Z run shows the difference was within run-to-run noise.

### Cases that changed

| Pair | Fixed | Broken |
|---|---|---|
| Held-out | `ho_control_V-063`, `ho_code_ERR_402` | `ho_code_ERR_101`, `ho_code_ERR_801` |
| Golden run 2 | `syn_overload_V-007`, `syn_speed_cap_V-055`, `syn_speed_cap_V-088`, `syn_charger_V-091`, `adv_holdout_brakes`, `prm_nonbreaking_hyphen` | `syn_sales_south_q2_vs_q3`, `doc_SB-114_sustained_overload`, `adv_nonexistent_vehicle`, `adv_mode_not_available`, `adv_south_outperformed`, `prm_ride_mode_limits` |

The four held-out flips are all error-code answers that leave out the recommended action (judge 2/4/4); A' does the same on other codes. They look like writer noise, not an effect of the fixes.

## Speed: the pass rate at the app's 45 s limit

| Run | 120 s (eval) | 45 s (app) | Budget waits not counted | Mean budget wait |
|---|---|---|---|---|
| A' held-out | 75.0% | 72.9% | 75.0% | 6.8 s |
| X+Y+Z held-out | 75.0% | **54.2%** | 72.9% | 17.7 s |
| A' golden run 1 | 56.5% | 39.1% | not logged | — |
| A' golden run 2 | 60.9% | 41.3% | not logged | — |
| X+Y+Z golden run 1 | 65.2% | 34.8% | not logged | — |
| X+Y+Z golden run 2 | 60.9% | **34.8%** | 60.9% | 22.6 s |

With the waits removed the two versions are equal. The 45 s gap is the waiting itself: the X+Y+Z planner prompt is longer (rules from X, Y and Z), so a question uses more tokens and waits more often for the free-tier allowance (8K tokens per minute). In X+Y+Z golden run 2, 24 of 46 cases waited more than 10 s.

## What this means

1. Fixes X, Y and Z do not raise accuracy. They move failures between cases.
2. They make the system slower, which is what users see (45 s limit).
3. The 48 held-out questions are all `synthetic_data` (database) questions, so they test X and Y more than Z. The golden pair covers document questions.
4. The held-out set has now been looked at once. Design work uses the golden set only; held-out is kept for judging the next result.

## Next

Step 5, speed: make a question fit in the per-minute token allowance, judged at the 45 s limit with the pass rate without waits as a counter-metric. Start from A' and keep only the changes that fix a bug checkable without the model (fix Y1, the weighted baseline), not the prompt rules that did not pay off. Rules for Step 5 are written before its runs.

Test logs: `heldout_Aprime_test_log.txt`, `heldout_XYZ_test_log.txt`, `result_XYZ_run2_test_log.txt`.
