# Baseline A - evaluation record

Run `20261118-112737`, golden set (54 cases: 46 scored questions + 8 abstention traps), one commit: `a21bfb8`.
This is the "before" number for the resume line *pass rate A% -> B% after fix X*. Every later run is compared with it.

## Result

| | Pass rate | 95% interval |
|---|---|---|
| **All 46 scored questions (the A number)** | **43.5%** (20 of 46) | 30.2 - 57.8 |
| Synthetic (27 cases: 19 data + 8 document questions) | 55.6% (15 of 27) | 37.3 - 72.4 |
| Hand-written (19 cases: 14 adversarial + 5 promoted past failures) | 26.3% (5 of 19) | 11.8 - 48.8 |
| Abstention traps (8 cases, scored separately) | 8 of 8 | - |

- The harness headline is the lower of synthetic / hand-written (26.3%). The resume line uses the all-46 figure; A and B must use the same definition.
- Traps: 0% ungrounded-truth rate, 100% abstention rate.
- Code checks passed 60.9% of the time. Judge means (1-4): completeness 2.48, correctness 3.46, hedging 2.87.

## What the number means

Each of the 46 questions has a reference answer and checks: the right tools were used, with the right arguments; the key numbers appear; the stop reason is allowed; citations are grounded. A cross-family judge model (a different family from the agent) also scores completeness, correctness and hedging, and a case passes only when the code checks and the judge both pass. 43.5% means 20 of 46 questions passed everything.

The interval (30.2 - 57.8) says what 46 questions can and cannot show: a later run must differ by more than the interval's width, or change specific cases consistently, to count as evidence.

## Findings

1. **Time is the biggest failure.** 17 of 26 failures ended on the 120 s limit (`stop_reason: timeout`). None of those 17 passed; 16 of the 23 questions that finished within 60 s passed. The harness flagged none of them as slowed by the provider's rate limit.
2. **Rejected SQL drives the timeouts.** 12 of the 17 timeouts include at least one query the database rejected (grouping, a missing column, a type error); each rejection costs a retry. 3 more only returned empty results.
3. **Run-to-run noise is large.** An earlier run on slightly different code scored 45.7% (21 of 46). The totals differ by one case, but **13 of 46 individual cases changed result** (7 pass to fail, 6 fail to pass). Per-case flips are expected with a stochastic model; a fix is shown by a consistent change across its target cases, not by one case.
4. **Safety behaviour is solid.** All 8 traps abstained; none produced an ungrounded answer.
5. **Hand-written cases are harder.** All 5 promoted past failures fail again.

### Cause of the 26 failures

| Cause | Cases |
|---|---|
| Ran out of the 120 s limit (12 with a rejected query, 3 with empty results, 2 with neither) | 17 |
| Diagnosis missed the fact that explains the problem | 1 (`syn_speed_cap_V-055`) |
| Answer taken from the wrong source (SQL where the document holds it) | 2 (`adv_holdout_partner_discount`, `adv_longest_range`) |
| Wrong values (`prm_ride_mode_limits`), missing currency (`syn_sales_flagship_avg_price_2026`), contradicts the reference (`doc_SB-135_firmware_top_mode_speed_cap`), missed the expected instruction (`doc_kb_range_shortfall_faq`), reference asks for more (`adv_mode_not_available`) | 5 |
| Right answer, but the expected-domain check disagrees (judge 4/4/4) | 1 (`adv_cross_domain`) |

## Setup

- Models: cheap `openai/gpt-oss-20b`, strong `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b`. No fallback model during evaluation.
- Free-tier Groq limits per organization and model: 8K tokens/min, 200K tokens/day. The run took three sessions (it resumed twice after the daily limit) on one commit; `--resume` now refuses to continue if the code changed.
- Time limit per question 120 s (the app allows 45 s). A model call may wait up to 100 s for token budget (`evaluation.queue_wait_s`).
- Telemetry export was off for this run.

## Method notes and limits

- A case is marked `infra` and re-run (not scored) when every tool call failed, the provider refused or could not be reached, or the judge call failed; a run stops after 3 in a row.
- `adv_typo_vehicle` (419 s) and `prm_ride_mode_limits` (302 s) overran the limit during the run, after a tool-server connection error. Both were re-run on the same commit (run `20261119-132135`): both failed again, in 120 s and 22 s. The original results stand.
- Judge-human agreement has not been measured yet, so judge scores are not independently confirmed.

## Files

- `baseline_A_cases.json` - per-case result (checks, judge scores, time, tool calls, rejected queries). Answer text is not included.
- `baseline_A_test_log.txt` - the console logs combined, in order.

## Per-case results

| Case | Source | Result | Failed checks | Judge (C/Cr/H) | Time | Stop | Rejected SQL |
|---|---|---|---|---|---|---|---|
| `syn_overload_V-042` | synthetic_data | FAIL | stop_reason, facts | 1/4/2 | 120 s | timeout | 0 |
| `syn_overload_V-007` | synthetic_data | FAIL | stop_reason, facts, must_contain | 1/1/1 | 120 s | timeout | 1 |
| `syn_speed_cap_V-055` | synthetic_data | FAIL | facts | 1/1/1 | 20 s | complete | 0 |
| `syn_speed_cap_V-088` | synthetic_data | FAIL | stop_reason, facts | 2/4/3 | 120 s | timeout | 1 |
| `syn_charger_V-091` | synthetic_data | FAIL | stop_reason, facts | 1/4/2 | 120 s | timeout | 2 |
| `syn_cell_wear_V-036` | synthetic_data | FAIL | stop_reason, facts | 2/4/3 | 120 s | timeout | 3 |
| `syn_firmware_V-055` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 |
| `syn_firmware_V-009` | synthetic_data | pass | - | 4/4/4 | 7 s | complete | 0 |
| `syn_cell_health_V-042` | synthetic_data | FAIL | stop_reason, facts | 1/4/2 | 120 s | timeout | 0 |
| `syn_odometer_V-101` | synthetic_data | pass | - | 4/4/4 | 6 s | complete | 0 |
| `syn_city_V-064` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 |
| `syn_code_ERR_205` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 |
| `syn_code_ERR_303` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 |
| `syn_code_ERR_403` | synthetic_data | pass | - | 4/4/4 | 6 s | complete | 0 |
| `syn_sales_top_model_south_q3` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 |
| `syn_sales_top_region_q3` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 |
| `syn_sales_flagship_avg_price_2026` | synthetic_data | FAIL | - | 1/1/1 | 5 s | complete | 0 |
| `syn_sales_fleet_aug` | synthetic_data | pass | - | 4/4/4 | 7 s | complete | 0 |
| `syn_sales_south_q2_vs_q3` | synthetic_data | pass | - | 4/4/4 | 63 s | complete | 0 |
| `doc_kb_range_shortfall_faq` | synthetic_doc | FAIL | must_contain | 1/2/2 | 13 s | complete | 0 |
| `doc_manual_ride_modes_range_and_charging` | synthetic_doc | pass | - | 4/4/4 | 14 s | complete | 0 |
| `doc_sales_q2_2026_review` | synthetic_doc | pass | - | 4/4/4 | 63 s | complete | 0 |
| `doc_sales_q3_2026_midquarter` | synthetic_doc | pass | - | 4/4/4 | 18 s | complete | 0 |
| `doc_SB-114_sustained_overload` | synthetic_doc | FAIL | facts | 1/4/2 | 120 s | timeout | 2 |
| `doc_SB-121_removable_pack_cell_wear` | synthetic_doc | pass | - | 3/4/4 | 63 s | complete | 0 |
| `doc_SB-135_firmware_top_mode_speed_cap` | synthetic_doc | FAIL | - | 2/4/2 | 63 s | no_new_evidence | 1 |
| `doc_SB-140_charger_low_power` | synthetic_doc | FAIL | facts | 1/4/1 | 120 s | timeout | 0 |
| `adv_nonexistent_vehicle` | adversarial | FAIL | must_contain | 1/1/1 | 120 s | timeout | 1 |
| `adv_typo_vehicle` | adversarial | FAIL | - | 2/4/2 | 419 s | cap_reached | 0 |
| `adv_nonexistent_model` | adversarial | pass | - | 4/4/4 | 14 s | complete | 0 |
| `adv_refute_claim` | adversarial | FAIL | - | 1/1/1 | 120 s | timeout | 1 |
| `adv_missing_metric` | adversarial | pass | - | 4/4/4 | 3 s | no_new_evidence | 0 |
| `adv_ambiguous` | adversarial | FAIL | - | 1/4/2 | 120 s | timeout | 1 |
| `adv_cross_domain` | adversarial | FAIL | domains | 4/4/4 | 8 s | complete | 0 |
| `adv_holdout_brakes` | adversarial | FAIL | - | 1/4/2 | 120 s | timeout | 0 |
| `adv_holdout_partner_discount` | adversarial | FAIL | tools, facts | 2/2/2 | 6 s | complete | 0 |
| `adv_holdout_delivery_story` | adversarial | pass | - | 4/4/4 | 63 s | complete | 0 |
| `adv_launch_timing` | adversarial | pass | - | 4/4/4 | 5 s | complete | 0 |
| `adv_longest_range` | adversarial | FAIL | facts, must_contain | 1/1/1 | 13 s | complete | 0 |
| `adv_mode_not_available` | adversarial | FAIL | - | 2/4/4 | 14 s | complete | 0 |
| `adv_south_outperformed` | adversarial | pass | - | 4/4/4 | 14 s | complete | 0 |
| `prm_ride_mode_limits` | promoted | FAIL | facts | 1/4/4 | 302 s | timeout | 0 |
| `prm_charger_doc_missed` | promoted | FAIL | facts | 1/4/2 | 120 s | timeout | 2 |
| `prm_battery_doc_missed` | promoted | FAIL | facts | 1/4/2 | 120 s | timeout | 2 |
| `prm_undocumented_overclaimed` | promoted | FAIL | - | 1/1/1 | 120 s | timeout | 1 |
| `prm_nonbreaking_hyphen` | promoted | FAIL | grounded | 1/4/2 | 120 s | timeout | 1 |
| `trap_liion_inventor` | trap | pass | - | 4/4/4 | 64 s | no_new_evidence | 0 |
| `trap_cell_chemistry` | trap | pass | - | 4/4/4 | 33 s | complete | 0 |
| `trap_cell_voltage` | trap | pass | - | 4/4/4 | 65 s | no_new_evidence | 0 |
| `trap_fame_amount` | trap | pass | - | 4/4/4 | 120 s | timeout | 0 |
| `trap_ip_meaning` | trap | pass | - | 4/4/4 | 79 s | complete | 0 |
| `trap_tyre_pressure` | trap | pass | - | 4/4/4 | 120 s | timeout | 0 |
| `trap_petrol_co2` | trap | pass | - | 4/4/4 | 64 s | no_new_evidence | 0 |
| `trap_riding_age` | trap | pass | - | 4/4/4 | 120 s | timeout | 0 |
