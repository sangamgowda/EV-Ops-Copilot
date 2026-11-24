# Baseline A - evaluation record

The "before" number for the resume line *pass rate A% -> B% after fix X*. Every later run is compared with it.

**A = 56.5%** (26 of 46 scored questions), 95% interval 42.2 - 69.8. Abstention traps: 8 of 8.

## How A was measured (two runs, one correction)

| Run | What | Commit | Clock |
|---|---|---|---|
| `20261118-112737` | all 54 golden cases | `a21bfb8` | real clock (19 November) |
| `20261119-140027` | the 20 cases whose queries used time windows after the data ended | `ed62219` | pinned to 2026-10-02 |

The first full run found a problem in the exam, not the assistant: the seeded data ends on 2026-10-01 and the golden answers were computed on 2026-10-02, but "this week" followed the real clock. In November every time-relative question searched an empty window, the planner kept widening it, and cases ran out of the 120 s limit. The 20 cases that used such windows passed 0 of 20; the other 26 passed 20 of 26.

The evaluation now gives the planner a fixed "today" (`evaluation.as_of: 2026-10-02`, commit `ed62219`); the app itself still uses the real clock. The 20 affected cases were re-run on that date. The 34 other results come from the full run. The two runs use the same models, prompts and code except the date pin (`git diff a21bfb8 ed62219`), and each case is scored independently, so combining them is sound; it is stated here so nothing is hidden. The number before the correction was 43.5% (20 of 46): the date pin is a correction to the measurement, **not** an improvement of the assistant, and is not counted in "after fix X".

## Result

| | Pass rate | 95% interval |
|---|---|---|
| **All 46 scored questions (the A number)** | **56.5%** (26 of 46) | 42.2 - 69.8 |
| Synthetic (27 cases: data and document questions) | 66.7% (18 of 27) | 47.8 - 81.4 |
| Hand-written (19 cases: adversarial and promoted past failures) | 42.1% (8 of 19) | 23.1 - 63.7 |
| Abstention traps (8 cases, scored separately) | 8 of 8 | - |

The harness headline is the lower of synthetic / hand-written; the resume line uses the all-46 figure, and A and B must use the same definition.

## What the number means

Each question has a reference answer and checks: the right tools with the right arguments, the key numbers present, an allowed stop reason, grounded citations. A cross-family judge model (a different family from the agent) scores completeness, correctness and hedging, and a case passes only when the code checks and the judge both pass. 56.5% means 26 of 46 questions passed everything. The interval says what 46 questions can and cannot show: B must clear it, or improve its target cases consistently, to count as evidence.

## Findings

1. **The date pin removed every empty result.** Across the 20 re-run cases, empty search results went from 16 to 0, and passes from 0 to 6.
2. **Time is still the main failure.** 12 of the 20 re-run cases still hit the 120 s limit. Across all 46 cases, 13 ended on the limit; questions that finished within 60 s passed 16 of 22.
3. **Rejected SQL is the next cause.** The re-run cases still sent 14 queries the database rejected, and 9 of the 13 timeouts include one: a column outside `GROUP BY` mixed with an aggregate, `round()` on a value not cast to `numeric`, or a wrong column name. Each rejection costs a retry.
4. **Per-case results move between runs** (a language model is not deterministic), so judge a fix by consistent change across its target cases, not by one case.
5. **Safety behaviour is solid.** All 8 traps abstained; none produced an ungrounded answer.

## Setup

- Models: cheap `openai/gpt-oss-20b`, strong `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b`. No fallback model during evaluation.
- Free-tier Groq limits per organization and model: 8K tokens/min, 200K tokens/day; the work took several sessions and `--resume` refuses to continue on different code.
- 120 s per question (the app allows 45 s). A model call may wait up to 100 s for token budget (`evaluation.queue_wait_s`). Telemetry export off.

## Method notes and limits

- A case is marked `infra` and re-run when every tool call failed, the provider refused or could not be reached, or the judge call failed; a run stops after 3 in a row.
- `adv_typo_vehicle` and `prm_ride_mode_limits` once overran the limit after a tool-server connection error; they were re-run (`20261119-132135`) and failed again, so the result stood. Both were later re-run on the pinned date; `adv_typo_vehicle` now passes.
- Judge-human agreement has not been measured, so judge scores are not independently confirmed.

## Files

- `logs/baseline_A_cases.json`: per-case result without answer text. `logs/baseline_A_test_log.txt`: the console logs combined, in order.

## Per-case results

| Case | Source | Result | Failed checks | Judge (C/Cr/H) | Time | Stop | Rejected SQL | Measured in |
|---|---|---|---|---|---|---|---|---|
| `syn_overload_V-042` | synthetic_data | pass | - | 4/4/4 | 107 s | complete | 0 | pinned-date run |
| `syn_overload_V-007` | synthetic_data | FAIL | stop_reason, facts, must_contain | 1/1/1 | 120 s | timeout | 1 | pinned-date run |
| `syn_speed_cap_V-055` | synthetic_data | FAIL | stop_reason, facts | 2/4/3 | 120 s | timeout | 2 | pinned-date run |
| `syn_speed_cap_V-088` | synthetic_data | pass | - | 4/3/4 | 62 s | complete | 0 | pinned-date run |
| `syn_charger_V-091` | synthetic_data | FAIL | stop_reason, must_contain | 1/4/4 | 120 s | timeout | 1 | pinned-date run |
| `syn_cell_wear_V-036` | synthetic_data | FAIL | stop_reason, facts | 1/4/2 | 120 s | timeout | 1 | pinned-date run |
| `syn_firmware_V-055` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 | full run |
| `syn_firmware_V-009` | synthetic_data | pass | - | 4/4/4 | 7 s | complete | 0 | full run |
| `syn_cell_health_V-042` | synthetic_data | pass | - | 4/3/4 | 62 s | complete | 0 | pinned-date run |
| `syn_odometer_V-101` | synthetic_data | pass | - | 4/4/4 | 6 s | complete | 0 | full run |
| `syn_city_V-064` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 | full run |
| `syn_code_ERR_205` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 | full run |
| `syn_code_ERR_303` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 | full run |
| `syn_code_ERR_403` | synthetic_data | pass | - | 4/4/4 | 6 s | complete | 0 | full run |
| `syn_sales_top_model_south_q3` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 | full run |
| `syn_sales_top_region_q3` | synthetic_data | pass | - | 4/4/4 | 5 s | complete | 0 | full run |
| `syn_sales_flagship_avg_price_2026` | synthetic_data | FAIL | - | 1/1/1 | 6 s | complete | 0 | pinned-date run |
| `syn_sales_fleet_aug` | synthetic_data | pass | - | 4/4/4 | 7 s | complete | 0 | full run |
| `syn_sales_south_q2_vs_q3` | synthetic_data | pass | - | 4/4/4 | 63 s | complete | 0 | full run |
| `doc_kb_range_shortfall_faq` | synthetic_doc | FAIL | must_contain | 1/2/2 | 13 s | complete | 0 | full run |
| `doc_manual_ride_modes_range_and_charging` | synthetic_doc | pass | - | 4/4/4 | 14 s | complete | 0 | full run |
| `doc_sales_q2_2026_review` | synthetic_doc | pass | - | 4/4/4 | 63 s | complete | 0 | full run |
| `doc_sales_q3_2026_midquarter` | synthetic_doc | pass | - | 4/4/4 | 18 s | complete | 0 | full run |
| `doc_SB-114_sustained_overload` | synthetic_doc | FAIL | facts | 1/4/2 | 120 s | timeout | 2 | pinned-date run |
| `doc_SB-121_removable_pack_cell_wear` | synthetic_doc | pass | - | 3/4/4 | 63 s | complete | 0 | full run |
| `doc_SB-135_firmware_top_mode_speed_cap` | synthetic_doc | FAIL | - | 2/4/2 | 120 s | timeout | 2 | pinned-date run |
| `doc_SB-140_charger_low_power` | synthetic_doc | FAIL | facts | 1/2/1 | 120 s | timeout | 0 | pinned-date run |
| `adv_nonexistent_vehicle` | adversarial | pass | - | 4/4/4 | 65 s | no_new_evidence | 0 | pinned-date run |
| `adv_typo_vehicle` | adversarial | pass | - | 4/4/4 | 62 s | complete | 0 | pinned-date run |
| `adv_nonexistent_model` | adversarial | pass | - | 4/4/4 | 14 s | complete | 0 | full run |
| `adv_refute_claim` | adversarial | pass | - | 4/4/3 | 67 s | complete | 0 | pinned-date run |
| `adv_missing_metric` | adversarial | pass | - | 4/4/4 | 3 s | no_new_evidence | 0 | full run |
| `adv_ambiguous` | adversarial | FAIL | - | 1/4/2 | 120 s | timeout | 0 | pinned-date run |
| `adv_cross_domain` | adversarial | FAIL | domains | 4/4/4 | 8 s | complete | 0 | full run |
| `adv_holdout_brakes` | adversarial | FAIL | - | 1/4/2 | 120 s | timeout | 0 | full run |
| `adv_holdout_partner_discount` | adversarial | FAIL | tools, facts | 2/2/2 | 6 s | complete | 0 | full run |
| `adv_holdout_delivery_story` | adversarial | pass | - | 4/4/4 | 63 s | complete | 0 | full run |
| `adv_launch_timing` | adversarial | pass | - | 4/4/4 | 5 s | complete | 0 | full run |
| `adv_longest_range` | adversarial | FAIL | facts, must_contain | 1/1/1 | 13 s | complete | 0 | full run |
| `adv_mode_not_available` | adversarial | FAIL | - | 2/4/4 | 14 s | complete | 0 | full run |
| `adv_south_outperformed` | adversarial | pass | - | 4/4/4 | 14 s | complete | 0 | full run |
| `prm_ride_mode_limits` | promoted | FAIL | facts | 4/1/1 | 68 s | complete | 1 | pinned-date run |
| `prm_charger_doc_missed` | promoted | FAIL | facts, must_contain | 1/4/4 | 120 s | timeout | 2 | pinned-date run |
| `prm_battery_doc_missed` | promoted | FAIL | facts | 1/4/2 | 120 s | timeout | 0 | pinned-date run |
| `prm_undocumented_overclaimed` | promoted | FAIL | - | 1/1/1 | 120 s | timeout | 1 | pinned-date run |
| `prm_nonbreaking_hyphen` | promoted | FAIL | grounded | 4/4/4 | 120 s | timeout | 1 | pinned-date run |
| `trap_liion_inventor` | trap | pass | - | 4/4/4 | 64 s | no_new_evidence | 0 | full run |
| `trap_cell_chemistry` | trap | pass | - | 4/4/4 | 33 s | complete | 0 | full run |
| `trap_cell_voltage` | trap | pass | - | 4/4/4 | 65 s | no_new_evidence | 0 | full run |
| `trap_fame_amount` | trap | pass | - | 4/4/4 | 120 s | timeout | 0 | full run |
| `trap_ip_meaning` | trap | pass | - | 4/4/4 | 79 s | complete | 0 | full run |
| `trap_tyre_pressure` | trap | pass | - | 4/4/4 | 120 s | timeout | 0 | full run |
| `trap_petrol_co2` | trap | pass | - | 4/4/4 | 64 s | no_new_evidence | 0 | full run |
| `trap_riding_age` | trap | pass | - | 4/4/4 | 120 s | timeout | 0 | full run |
