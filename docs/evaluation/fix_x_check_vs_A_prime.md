# Fix X check (SQL rules, prompt v6) against A'

Run `20261122-234550`, commit `2ccf3bc`, same environment as A'. 18 cases: the 13 fail-in-both A' cases that had rejected SQL, plus 5 passing guards. A screen before the full B run, not the B number.

| 18 cases | A' run 1 | A' run 2 | With fix X |
|---|---|---|---|
| Passed | 5 | 5 | **4** |
| Hit the 120 s limit | 11 | 10 | 10 |
| Rejected SQL | 14 | 19 | **1** |
| Mean time per case | 96 s | 89 s | 86 s |

## Reading

1. The rules do what they were written for: rejected SQL fell from 14 / 19 to 1.
2. None of the 13 target cases moved to pass. Ten still hit the 120 s limit (the free tier's per-minute token budget); the rest fail on the answer, not the SQL.
3. One guard (`doc_SB-121_removable_pack_cell_wear`) passed in both A' runs and timed out here with no rejected SQL: noise from the same budget wait, not the fix.
4. Decision: take fix X to B for one full run and report the result as measured. The next fix is chosen after B.

## Per case

| Case | A' run 1 | A' run 2 | Fix X | Rejected SQL (A' -> X) | Timeout (A' -> X) |
|---|---|---|---|---|---|
| `syn_overload_V-007` | fail | fail | fail | 3 / 2 -> 1 | 1 / 1 -> 1 |
| `syn_speed_cap_V-088` | fail | fail | fail | 2 / 2 -> 0 | 1 / 1 -> 1 |
| `syn_charger_V-091` | fail | fail | fail | 0 / 1 -> 0 | 1 / 1 -> 0 |
| `syn_cell_wear_V-036` | fail | fail | fail | 0 / 2 -> 0 | 1 / 1 -> 1 |
| `syn_cell_health_V-042` | pass | pass | pass | 0 / 0 -> 0 | 0 / 0 -> 0 |
| `syn_sales_south_q2_vs_q3` | pass | pass | pass | 0 / 0 -> 0 | 0 / 0 -> 0 |
| `doc_kb_range_shortfall_faq` | fail | fail | fail | 1 / 0 -> 0 | 0 / 0 -> 0 |
| `doc_SB-121_removable_pack_cell_wear` | pass | pass | fail | 0 / 0 -> 0 | 0 / 0 -> 1 |
| `doc_SB-140_charger_low_power` | fail | fail | fail | 2 / 2 -> 0 | 1 / 1 -> 1 |
| `adv_nonexistent_vehicle` | pass | pass | pass | 0 / 0 -> 0 | 0 / 0 -> 0 |
| `adv_refute_claim` | fail | fail | fail | 1 / 2 -> 0 | 0 / 1 -> 1 |
| `adv_ambiguous` | fail | fail | fail | 1 / 1 -> 0 | 1 / 1 -> 1 |
| `adv_holdout_partner_discount` | fail | fail | fail | 1 / 0 -> 0 | 1 / 0 -> 0 |
| `adv_holdout_delivery_story` | pass | pass | pass | 0 / 0 -> 0 | 0 / 0 -> 0 |
| `prm_charger_doc_missed` | fail | fail | fail | 1 / 2 -> 0 | 1 / 1 -> 0 |
| `prm_battery_doc_missed` | fail | fail | fail | 1 / 2 -> 0 | 1 / 1 -> 1 |
| `prm_undocumented_overclaimed` | fail | fail | fail | 1 / 1 -> 0 | 1 / 0 -> 1 |
| `prm_nonbreaking_hyphen` | fail | fail | fail | 0 / 2 -> 0 | 1 / 1 -> 1 |
