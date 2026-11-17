# Baseline A — evaluation record (provisional: mixed code versions)

Run `20261001-132338`, golden set (54 cases: 46 scored questions + 8 abstention traps).
This is the "before" number for the resume line *pass rate A% → B% after fix X*.
Every later run is compared with this one.

## Result

| | Pass rate | 95% interval |
|---|---|---|
| **All 46 scored questions (the A number)** | **45.7%** (21 of 46) | 32.2 – 59.8 |
| Synthetic (27 cases: 19 data + 8 document questions) | 51.9% (14 of 27) | 34.0 – 69.3 |
| Hand-written (19 cases: 14 adversarial + 5 promoted past failures) | 36.8% (7 of 19) | 19.1 – 59.0 |
| Abstention traps (8 cases, scored separately) | 8 of 8 | – |

- Harness headline = the lower of synthetic / hand-written = 36.8%. The resume line uses the all-46 figure; A and B must use the same definition.
- Traps: 0% ungrounded-truth rate, 100% abstention rate.
- Code checks passed 63.0% of the time. Judge means (1–4): completeness 2.78, correctness 3.33, hedging 3.24.

## Setup

- Models: cheap `openai/gpt-oss-20b`, strong `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b` (a different model family from the agent). No fallback model during evaluation.
- Free-tier Groq limits per model: 8K tokens/min, 200K tokens/day. The evaluation allows 120 s per question (the app allows 45 s) and lets one model call wait up to 100 s for token budget (`evaluation.queue_wait_s`).
- Code: **this run mixes two code versions.** The first 33 cases were scored at commit `61568e7`; the remaining 21 at `31c7b3d` plus the evaluation changes in this PR. Between those commits the LLM client, MCP client, retrieval/embedding code, SQL validator and the golden case files themselves changed (`git diff 61568e7 31c7b3d`). The run was resumed, and resuming does not check the commit.
- **Status: provisional.** Because of the mixed versions, A is not a clean single-commit baseline. Before fix X is measured, a full re-run of all 54 cases on one commit (the commit that carries this record) should replace it, so A and B differ only by the fix. Resume-line numbers should come from that clean run.

## What went wrong in A (25 failures)

| Cause | Cases |
|---|---|
| Diagnosis misses the one fact that explains the problem (cell health, current draw, firmware, payload rating) | 7 |
| Answer taken from the wrong source (telemetry/SQL instead of the document, or the reverse) | 6 |
| Ran out of the 120 s limit after 5–6 tool calls (several with rejected SQL) | 6 |
| Right answer, but a strict check disagrees (judge scored 4/4/4) | 3 |
| Missing currency or unit | 1 |
| Reference answer arguable | 2 |

The six timed-out cases were re-run twice (`Check 1`, `Check 2` in the test log): all timed out again with no rate-limit or connection errors, so they are real failures of the agent, not of the free tier.

## Method notes and limits

- A case is marked `infra` and re-run (not scored) when every tool call failed or the provider refused a step; a run stops after 3 such cases in a row. A network outage during `Check 1` was *not* detected as infra (the harness scored it as an agent failure); those 5 results were discarded and re-run.
- Judge–human agreement has not been measured yet.
- With 46 cases the interval is wide (±14 points). A change smaller than that is not evidence on its own; the per-case diff and the held-out set (48 cases) are.

## Files

- `baseline_A_cases.json` — per-case result (checks, judge scores, time, tool count). Answer text is not included.
- `baseline_A_test_log.txt` — the six console logs combined, in order.

## Per-case results

| Case | Source | Result | Failed checks | Judge (C/Cr/H) | Time | Stop |
|---|---|---|---|---|---|---|
| `syn_overload_V-042` | synthetic_data | FAIL | – | 2/2/4 | 34 s | complete |
| `syn_overload_V-007` | synthetic_data | FAIL | key_args, facts | 2/4/4 | 48 s | complete |
| `syn_speed_cap_V-055` | synthetic_data | FAIL | – | 1/1/1 | 51 s | complete |
| `syn_speed_cap_V-088` | synthetic_data | pass | – | 3/3/4 | 26 s | complete |
| `syn_charger_V-091` | synthetic_data | FAIL | – | 3/2/4 | 48 s | complete |
| `syn_cell_wear_V-036` | synthetic_data | FAIL | key_args, stop_reason, facts | 1/1/1 | 65 s | cap_reached |
| `syn_firmware_V-055` | synthetic_data | pass | – | 4/4/4 | 6 s | complete |
| `syn_firmware_V-009` | synthetic_data | pass | – | 4/4/4 | 5 s | complete |
| `syn_cell_health_V-042` | synthetic_data | pass | – | 4/3/4 | 7 s | complete |
| `syn_odometer_V-101` | synthetic_data | pass | – | 4/4/4 | 10 s | complete |
| `syn_city_V-064` | synthetic_data | FAIL | domains | 4/4/4 | 5 s | complete |
| `syn_code_ERR_205` | synthetic_data | pass | – | 4/4/4 | 6 s | complete |
| `syn_code_ERR_303` | synthetic_data | pass | – | 4/4/4 | 4 s | complete |
| `syn_code_ERR_403` | synthetic_data | pass | – | 4/4/4 | 5 s | complete |
| `syn_sales_top_model_south_q3` | synthetic_data | pass | – | 4/4/4 | 5 s | complete |
| `syn_sales_top_region_q3` | synthetic_data | pass | – | 4/4/4 | 5 s | complete |
| `syn_sales_flagship_avg_price_2026` | synthetic_data | FAIL | – | 1/1/1 | 9 s | complete |
| `syn_sales_fleet_aug` | synthetic_data | pass | – | 4/4/4 | 5 s | complete |
| `syn_sales_south_q2_vs_q3` | synthetic_data | pass | – | 4/4/4 | 9 s | complete |
| `doc_kb_range_shortfall_faq` | synthetic_doc | FAIL | must_contain | 1/2/2 | 14 s | complete |
| `doc_manual_ride_modes_range_and_charging` | synthetic_doc | pass | – | 4/4/4 | 19 s | complete |
| `doc_sales_q2_2026_review` | synthetic_doc | FAIL | tools, facts | 1/1/1 | 5 s | complete |
| `doc_sales_q3_2026_midquarter` | synthetic_doc | FAIL | tools, must_contain | 2/4/4 | 8 s | complete |
| `doc_SB-114_sustained_overload` | synthetic_doc | pass | – | 4/4/4 | 17 s | complete |
| `doc_SB-121_removable_pack_cell_wear` | synthetic_doc | FAIL | facts | 4/4/4 | 54 s | complete |
| `doc_SB-135_firmware_top_mode_speed_cap` | synthetic_doc | FAIL | – | 4/2/4 | 58 s | complete |
| `doc_SB-140_charger_low_power` | synthetic_doc | FAIL | facts | 1/1/1 | 45 s | cap_reached |
| `adv_nonexistent_vehicle` | adversarial | pass | – | 4/4/4 | 4 s | no_new_evidence |
| `adv_typo_vehicle` | adversarial | pass | – | 3/4/4 | 38 s | complete |
| `adv_nonexistent_model` | adversarial | FAIL | – | 2/4/4 | 16 s | complete |
| `adv_refute_claim` | adversarial | pass | – | 4/4/4 | 45 s | cap_reached |
| `adv_missing_metric` | adversarial | pass | – | 4/4/4 | 14 s | no_new_evidence |
| `adv_ambiguous` | adversarial | FAIL | – | 1/1/1 | 44 s | cap_reached |
| `adv_cross_domain` | adversarial | FAIL | domains | 4/4/4 | 6 s | complete |
| `adv_holdout_brakes` | adversarial | FAIL | must_contain | 1/4/4 | 120 s | timeout |
| `adv_holdout_partner_discount` | adversarial | FAIL | tools, facts | 2/2/2 | 7 s | complete |
| `adv_holdout_delivery_story` | adversarial | pass | – | 4/4/4 | 63 s | complete |
| `adv_launch_timing` | adversarial | pass | – | 4/4/4 | 6 s | complete |
| `adv_longest_range` | adversarial | FAIL | facts, must_contain | 1/4/2 | 20 s | complete |
| `adv_mode_not_available` | adversarial | pass | – | 3/4/4 | 14 s | complete |
| `adv_south_outperformed` | adversarial | FAIL | facts, must_contain | 1/4/2 | 120 s | timeout |
| `prm_ride_mode_limits` | promoted | FAIL | facts | 4/1/4 | 68 s | complete |
| `prm_charger_doc_missed` | promoted | FAIL | facts | 1/4/2 | 120 s | timeout |
| `prm_battery_doc_missed` | promoted | FAIL | facts | 2/4/2 | 120 s | timeout |
| `prm_undocumented_overclaimed` | promoted | FAIL | – | 1/4/2 | 120 s | timeout |
| `prm_nonbreaking_hyphen` | promoted | FAIL | grounded | 1/4/2 | 120 s | timeout |
| `trap_liion_inventor` | trap | pass | – | 4/4/4 | 62 s | no_new_evidence |
| `trap_cell_chemistry` | trap | pass | – | 4/4/4 | 14 s | complete |
| `trap_cell_voltage` | trap | pass | – | 4/4/4 | 64 s | no_new_evidence |
| `trap_fame_amount` | trap | pass | – | 4/4/4 | 63 s | no_new_evidence |
| `trap_ip_meaning` | trap | pass | – | 4/4/4 | 120 s | timeout |
| `trap_tyre_pressure` | trap | pass | – | 4/4/4 | 120 s | timeout |
| `trap_petrol_co2` | trap | pass | – | 4/4/4 | 63 s | no_new_evidence |
| `trap_riding_age` | trap | pass | – | 4/4/4 | 120 s | timeout |
