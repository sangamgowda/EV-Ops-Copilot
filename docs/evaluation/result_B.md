# B - evaluation record (A' + fix X)

Run `20261123-005044`, commit `ff8b918` (A' code `c5cedee` + fix X `2ccf3bc`; the later commit only adds the A' record). One full run of all 54 cases, free Groq keys, same exam, date (2026-10-02), 120 s limit and models as A'.

Fix X: SQL rules in the planner prompt (prompt v6) and a trimmed schema that hides the sales table from vehicle-only questions.

## Result against A'

| | A' run 1 | A' run 2 | **B** |
|---|---|---|---|
| Pass rate, 46 non-trap cases | 56.5% (26 of 46), 95% interval 42.2 - 69.8 | 60.9% (28 of 46), 95% interval 46.5 - 73.6 | **52.2% (24 of 46), 95% interval 38.1 - 65.9** |
| Abstention traps | 8 of 8 | 7 of 8 | 8 of 8 |
| Hit the 120 s limit (all failed) | 15 | 11 | 10 |
| Rejected SQL queries | 20 | 23 | **5** |

A' = 58.7% (mean of its two runs). B = 52.2%, i.e. **-6.5 points**, inside the noise of 46 cases (the three intervals overlap). Honest reading: rejected SQL fell from about 21 to 5, the pass rate did not improve.

Fixed (fail in both A' runs, pass in B): `syn_speed_cap_V-088`, `doc_sales_q2_2026_review`.
Broke (pass in both A' runs, fail in B): `syn_sales_flagship_avg_price_2026`, `adv_typo_vehicle`, `prm_ride_mode_limits`.

## Why the pass rate did not rise

1. Timeouts remain (10 cases, all failed): the free tier's per-minute token budget, which SQL rules cannot change. Seven of them had no rejected query at all.
2. Fix X's baseline template can return a wrong number silently. It computes the baseline with `max(b.nominal_value)`. For `current_draw` the baseline differs per ride mode (Saver 11.7, Eco 14.6, Normal 23.1, Sport 32.9, Sport+ 43.1), so `max` returns the Sport value (32.9) where the average over the readings is 20.1. For V-042 the measured 26.8 A then looks *below* baseline, and the answer says the vehicle is under its normal draw. Verified by running both queries on the database. This explains the cases that finish fast and fail on wrong numbers (`syn_overload_V-042`, `adv_typo_vehicle`, `prm_nonbreaking_hyphen`, `prm_undocumented_overclaimed`, `syn_charger_V-091`, `prm_charger_doc_missed`).
3. Causes fix X cannot touch: wrong currency (`syn_sales_flagship_avg_price_2026` says USD, reference INR), the discount answer taken from SQL instead of the documents (`adv_holdout_partner_discount`, `doc_sales_q3_2026_midquarter`), and a missing key sentence in retrieved text (`doc_kb_range_shortfall_faq`).

## Per case (time, rejected SQL queries)

| Case | Source | A' run 1 | A' run 2 | B |
|---|---|---|---|---|
| `syn_overload_V-042` | synthetic_data | fail (timeout), 120 s, rej 2 | pass, 62 s, rej 0 | fail, 62 s, rej 0 |
| `syn_overload_V-007` | synthetic_data | fail (timeout), 120 s, rej 3 | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 0 |
| `syn_speed_cap_V-055` | synthetic_data | pass, 63 s, rej 0 | fail, 19 s, rej 1 | fail, 22 s, rej 0 |
| `syn_speed_cap_V-088` | synthetic_data | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 2 | pass, 62 s, rej 0 |
| `syn_charger_V-091` | synthetic_data | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 1 | fail, 22 s, rej 0 |
| `syn_cell_wear_V-036` | synthetic_data | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 0 |
| `syn_firmware_V-055` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 | pass, 4 s, rej 0 |
| `syn_firmware_V-009` | synthetic_data | pass, 6 s, rej 0 | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_cell_health_V-042` | synthetic_data | pass, 63 s, rej 0 | pass, 63 s, rej 0 | pass, 63 s, rej 0 |
| `syn_odometer_V-101` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 | pass, 7 s, rej 0 |
| `syn_city_V-064` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_code_ERR_205` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_code_ERR_303` | synthetic_data | pass, 6 s, rej 0 | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_code_ERR_403` | synthetic_data | pass, 5 s, rej 0 | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_sales_top_model_south_q3` | synthetic_data | pass, 6 s, rej 0 | pass, 6 s, rej 0 | pass, 5 s, rej 0 |
| `syn_sales_top_region_q3` | synthetic_data | pass, 6 s, rej 0 | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `syn_sales_flagship_avg_price_2026` | synthetic_data | pass, 8 s, rej 0 | pass, 6 s, rej 0 | fail, 5 s, rej 0 |
| `syn_sales_fleet_aug` | synthetic_data | pass, 6 s, rej 0 | pass, 6 s, rej 0 | pass, 6 s, rej 0 |
| `syn_sales_south_q2_vs_q3` | synthetic_data | pass, 63 s, rej 0 | pass, 63 s, rej 0 | pass, 63 s, rej 0 |
| `doc_kb_range_shortfall_faq` | synthetic_doc | fail, 77 s, rej 1 | fail, 15 s, rej 0 | fail, 13 s, rej 0 |
| `doc_manual_ride_modes_range_and_charging` | synthetic_doc | pass, 12 s, rej 0 | pass, 14 s, rej 0 | pass, 13 s, rej 0 |
| `doc_sales_q2_2026_review` | synthetic_doc | fail, 6 s, rej 0 | fail, 6 s, rej 0 | pass, 62 s, rej 0 |
| `doc_sales_q3_2026_midquarter` | synthetic_doc | fail, 62 s, rej 0 | fail, 6 s, rej 0 | fail, 63 s, rej 0 |
| `doc_SB-114_sustained_overload` | synthetic_doc | fail (timeout), 120 s, rej 1 | pass, 63 s, rej 2 | fail (timeout), 120 s, rej 2 |
| `doc_SB-121_removable_pack_cell_wear` | synthetic_doc | pass, 73 s, rej 0 | pass, 63 s, rej 0 | pass, 62 s, rej 0 |
| `doc_SB-135_firmware_top_mode_speed_cap` | synthetic_doc | fail (timeout), 120 s, rej 2 | pass, 20 s, rej 1 | fail, 17 s, rej 0 |
| `doc_SB-140_charger_low_power` | synthetic_doc | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 1 |
| `adv_nonexistent_vehicle` | adversarial | pass, 63 s, rej 0 | pass, 63 s, rej 0 | pass, 62 s, rej 0 |
| `adv_typo_vehicle` | adversarial | pass, 64 s, rej 0 | pass, 62 s, rej 0 | fail, 64 s, rej 0 |
| `adv_nonexistent_model` | adversarial | pass, 14 s, rej 0 | pass, 18 s, rej 0 | pass, 12 s, rej 0 |
| `adv_refute_claim` | adversarial | fail, 12 s, rej 1 | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 0 |
| `adv_missing_metric` | adversarial | pass, 3 s, rej 0 | pass, 2 s, rej 0 | pass, 1 s, rej 0 |
| `adv_ambiguous` | adversarial | fail (timeout), 120 s, rej 1 | fail (timeout), 120 s, rej 1 | fail (timeout), 120 s, rej 0 |
| `adv_cross_domain` | adversarial | pass, 6 s, rej 0 | pass, 5 s, rej 0 | pass, 4 s, rej 0 |
| `adv_holdout_brakes` | adversarial | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 0 |
| `adv_holdout_partner_discount` | adversarial | fail (timeout), 120 s, rej 1 | fail, 7 s, rej 0 | fail, 6 s, rej 0 |
| `adv_holdout_delivery_story` | adversarial | pass, 63 s, rej 0 | pass, 62 s, rej 0 | pass, 62 s, rej 0 |
| `adv_launch_timing` | adversarial | pass, 7 s, rej 0 | pass, 5 s, rej 0 | pass, 5 s, rej 0 |
| `adv_longest_range` | adversarial | fail, 15 s, rej 0 | fail, 70 s, rej 0 | fail (timeout), 120 s, rej 1 |
| `adv_mode_not_available` | adversarial | pass, 17 s, rej 0 | pass, 16 s, rej 0 | pass, 13 s, rej 0 |
| `adv_south_outperformed` | adversarial | pass, 92 s, rej 0 | pass, 75 s, rej 0 | pass, 64 s, rej 0 |
| `prm_ride_mode_limits` | promoted | pass, 15 s, rej 1 | pass, 16 s, rej 0 | fail, 67 s, rej 1 |
| `prm_charger_doc_missed` | promoted | fail (timeout), 120 s, rej 1 | fail (timeout), 120 s, rej 2 | fail, 63 s, rej 0 |
| `prm_battery_doc_missed` | promoted | fail (timeout), 120 s, rej 1 | fail (timeout), 120 s, rej 2 | fail (timeout), 120 s, rej 0 |
| `prm_undocumented_overclaimed` | promoted | fail (timeout), 120 s, rej 1 | fail, 65 s, rej 1 | fail (timeout), 120 s, rej 0 |
| `prm_nonbreaking_hyphen` | promoted | fail (timeout), 120 s, rej 0 | fail (timeout), 120 s, rej 2 | fail, 62 s, rej 0 |
| `trap_liion_inventor` | trap | pass (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 |
| `trap_cell_chemistry` | trap | pass, 14 s, rej 0 | pass, 14 s, rej 0 | pass, 13 s, rej 0 |
| `trap_cell_voltage` | trap | pass, 63 s, rej 0 | pass, 62 s, rej 0 | pass, 62 s, rej 0 |
| `trap_fame_amount` | trap | pass, 62 s, rej 0 | pass (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 |
| `trap_ip_meaning` | trap | pass, 63 s, rej 0 | fail (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 |
| `trap_tyre_pressure` | trap | pass (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 | pass (timeout), 120 s, rej 0 |
| `trap_petrol_co2` | trap | pass, 63 s, rej 0 | pass, 62 s, rej 0 | pass, 65 s, rej 0 |
| `trap_riding_age` | trap | pass, 65 s, rej 0 | pass (timeout), 120 s, rej 0 | pass, 66 s, rej 0 |
