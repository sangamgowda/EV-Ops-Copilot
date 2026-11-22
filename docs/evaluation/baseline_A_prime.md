# Baseline A' - evaluation record

The fair "before" number for the plan in `evaluation_runs/eval_A_to_B_plan.md`. Same code, exam, date and time limit as B; the only later difference is fix X.

Code: commit `c5cedee` (`1dcc428` plus the two exam fixes; no SQL rules). Evaluation date 2026-10-02, 120 s per question, free Groq keys (8K tokens a minute, 200K a day per organization).
Models: cheap `openai/gpt-oss-20b`, strong `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b`.

## Result: two complete runs of all 54 cases

| | Run 1 `20261121-120508` | Run 2 `20261122-214903` |
|---|---|---|
| Pass rate, 46 non-trap cases | **56.5% (26 of 46), 95% interval 42.2 - 69.8** | **60.9% (28 of 46), 95% interval 46.5 - 73.6** |
| Abstention traps | 8 of 8 | 7 of 8 |
| Cases that hit the 120 s limit (all failed) | 15 | 11 |
| Rejected SQL queries | 20 | 23 |

**A' = 58.7%** (mean of the two runs; each is 26 and 28 of 46). 25 cases pass in both runs, 17 fail in both, 4 flip between runs: the run-to-run noise the plan warns about.

Exam fixes made before measuring (commit `c5cedee`): `adv_cross_domain` expects the business domain only; `adv_south_outperformed` also accepts "available", "exclusive" or "1 June" as naming the cause. Both had judge scores of 4/4/4 in every earlier run.

Run 1 took three keys over three provider days and run 2 two keys; runs stopped on the daily limit or on the internet dropping and were resumed with `--resume` on the same commit. Cases held back as infra were re-run, not counted as failures.

## Failure buckets (the 17 cases that fail in both runs)

| Bucket | Cases |
|---|---|
| Rejected SQL in at least one run | 13 |
| Hit the 120 s limit in both runs | 11 |
| Used SQL where the answer was in a document | 3 (`doc_sales_q2_2026_review`, `doc_sales_q3_2026_midquarter`, `adv_holdout_partner_discount`) |
| Missing key fact or number | 3 (`doc_kb_range_shortfall_faq`, `adv_longest_range`, `prm_undocumented_overclaimed`) |

The buckets overlap (a timed-out case often also had a rejected query). Fix X targets the first bucket.

## Per case (time, rejected SQL queries)

| Case | Source | Run 1 | Run 2 |
|---|---|---|---|
| `syn_overload_V-042` | synthetic_data | fail (timeout), 120 s, rej 2 | pass, 62 s, rej 0 |
| `syn_overload_V-007` | synthetic_data | fail (timeout), 120 s, rej 3 | fail (timeout), 120 s, rej 2 |
| `syn_speed_cap_V-055` | synthetic_data | pass, 63 s, rej 0 | fail, 19 s, rej 1 |
| `syn_speed_cap_V-088` | synthetic_data | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 2 |
| `syn_charger_V-091` | synthetic_data | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 1 |
| `syn_cell_wear_V-036` | synthetic_data | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 2 |
| `syn_firmware_V-055` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_firmware_V-009` | synthetic_data | pass, 6 s, rej 0 | pass, 5 s, rej 0 |
| `syn_cell_health_V-042` | synthetic_data | pass, 63 s, rej 0 | pass, 63 s, rej 0 |
| `syn_odometer_V-101` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_city_V-064` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_code_ERR_205` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_code_ERR_303` | synthetic_data | pass, 6 s, rej 0 | pass, 5 s, rej 0 |
| `syn_code_ERR_403` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_sales_top_model_south_q3` | synthetic_data | pass, 6 s, rej 0 | pass, 6 s, rej 0 |
| `syn_sales_top_region_q3` | synthetic_data | pass, 6 s, rej 0 | pass, 5 s, rej 0 |
| `syn_sales_flagship_avg_price_2026` | synthetic_data | pass, 8 s, rej 0 | pass, 6 s, rej 0 |
| `syn_sales_fleet_aug` | synthetic_data | pass, 6 s, rej 0 | pass, 6 s, rej 0 |
| `syn_sales_south_q2_vs_q3` | synthetic_data | pass, 63 s, rej 0 | pass, 63 s, rej 0 |
| `doc_kb_range_shortfall_faq` | synthetic_doc | fail, 77 s, rej 1 | fail, 15 s, rej 0 |
| `doc_manual_ride_modes_range_and_charging` | synthetic_doc | pass, 12 s, rej 0 | pass, 14 s, rej 0 |
| `doc_sales_q2_2026_review` | synthetic_doc | fail, 6 s, rej 0 | fail, 6 s, rej 0 |
| `doc_sales_q3_2026_midquarter` | synthetic_doc | fail, 62 s, rej 0 | fail, 6 s, rej 0 |
| `doc_SB-114_sustained_overload` | synthetic_doc | fail (timeout), 120 s, rej 1 | pass, 63 s, rej 2 |
| `doc_SB-121_removable_pack_cell_wear` | synthetic_doc | pass, 73 s, rej 0 | pass, 63 s, rej 0 |
| `doc_SB-135_firmware_top_mode_speed_cap` | synthetic_doc | fail (timeout), 120 s, rej 2 | pass, 20 s, rej 1 |
| `doc_SB-140_charger_low_power` | synthetic_doc | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 2 |
| `adv_nonexistent_vehicle` | adversarial | pass, 63 s, rej 0 | pass, 63 s, rej 0 |
| `adv_typo_vehicle` | adversarial | pass, 64 s, rej 0 | pass, 62 s, rej 0 |
| `adv_nonexistent_model` | adversarial | pass, 14 s, rej 0 | pass, 18 s, rej 0 |
| `adv_refute_claim` | adversarial | fail, 12 s, rej 1 | fail (timeout), 120 s, rej 2 |
| `adv_missing_metric` | adversarial | pass, 3 s, rej 0 | pass, 2 s, rej 0 |
| `adv_ambiguous` | adversarial | fail (timeout), 120 s, rej 1 | fail (timeout), 120 s, rej 1 |
| `adv_cross_domain` | adversarial | pass, 6 s, rej 0 | pass, 5 s, rej 0 |
| `adv_holdout_brakes` | adversarial | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 0 |
| `adv_holdout_partner_discount` | adversarial | fail (timeout), 120 s, rej 1 | fail, 7 s, rej 0 |
| `adv_holdout_delivery_story` | adversarial | pass, 63 s, rej 0 | pass, 62 s, rej 0 |
| `adv_launch_timing` | adversarial | pass, 7 s, rej 0 | pass, 5 s, rej 0 |
| `adv_longest_range` | adversarial | fail, 15 s, rej 0 | fail, 70 s, rej 0 |
| `adv_mode_not_available` | adversarial | pass, 17 s, rej 0 | pass, 16 s, rej 0 |
| `adv_south_outperformed` | adversarial | pass, 92 s, rej 0 | pass, 75 s, rej 0 |
| `prm_ride_mode_limits` | promoted | pass, 15 s, rej 1 | pass, 16 s, rej 0 |
| `prm_charger_doc_missed` | promoted | fail (timeout), 120 s, rej 1 | fail (timeout), 120 s, rej 2 |
| `prm_battery_doc_missed` | promoted | fail (timeout), 120 s, rej 1 | fail (timeout), 120 s, rej 2 |
| `prm_undocumented_overclaimed` | promoted | fail (timeout), 120 s, rej 1 | fail, 65 s, rej 1 |
| `prm_nonbreaking_hyphen` | promoted | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 2 |
| `trap_liion_inventor` | trap | pass (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 |
| `trap_cell_chemistry` | trap | pass, 14 s, rej 0 | pass, 14 s, rej 0 |
| `trap_cell_voltage` | trap | pass, 63 s, rej 0 | pass, 62 s, rej 0 |
| `trap_fame_amount` | trap | pass, 62 s, rej 0 | pass (timeout), 120 s, rej 0 |
| `trap_ip_meaning` | trap | pass, 63 s, rej 0 | fail (timeout), 120 s, rej 0 |
| `trap_tyre_pressure` | trap | pass (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 |
| `trap_petrol_co2` | trap | pass, 63 s, rej 0 | pass, 62 s, rej 0 |
| `trap_riding_age` | trap | pass, 65 s, rej 0 | pass (timeout), 120 s, rej 0 |
