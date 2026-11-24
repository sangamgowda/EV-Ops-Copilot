# Fix Y - check record (18 cases)

Run `20261123-113104`, commit `43d2cfd` (B code `2ccf3bc` + fix Y). A check on 18 cases only, not a full run: 9 baseline-number targets, 5 document/currency targets, 4 guards. Free Groq keys, same exam, date (2026-10-02), 120 s limit and models as A' and B. The run was stopped twice by the free-tier daily limit and resumed on another key; the three logs are joined in `logs/fix_y_check_test_log.txt`.

Fix Y1: the SQL template computes the baseline as a reading-weighted average instead of `max(b.nominal_value)`, and the planner prompt says baselines differ by ride mode (prompt v7).
Fix Y2: terms, limits and instructions go to documents, and prices are in rupees with an `_inr` alias; the writer says rupees (prompt v7, synthesize v4).

## Result on the same 18 cases

| | A' run 1 | A' run 2 | B | **Y** |
|---|---|---|---|---|
| Pass | 7 of 18 | 7 of 18 | 4 of 18 | **6 of 18** |

Fixed against B: `syn_speed_cap_V-055`, `prm_ride_mode_limits`, `adv_holdout_partner_discount`.
Broke against B: `syn_sales_south_q2_vs_q3`.
Still failing (10): `syn_overload_V-042`, `syn_overload_V-007`, `syn_charger_V-091`, `adv_typo_vehicle`, `prm_nonbreaking_hyphen`, `prm_undocumented_overclaimed`, `prm_charger_doc_missed`, `doc_sales_q3_2026_midquarter`, `syn_sales_flagship_avg_price_2026`, `doc_kb_range_shortfall_faq`, `adv_longest_range` (11 counting the flagship price case, which failed on the judge only).

Honest reading: no gain over A' on these cases (6 against 7). The check is too small to separate one or two cases from noise.

## Where the failures break (checked from the recorded tool calls and by direct retrieval tests)

| Where | Cases | What happens |
|---|---|---|
| Retrieval query wording (6) | `syn_overload_V-042`, `adv_typo_vehicle`, `prm_nonbreaking_hyphen`, `syn_overload_V-007`, `syn_charger_V-091`, `prm_charger_doc_missed` | The planner searches for the symptom ("range estimate dropped", "takes longer to charge"). These score 0.03 - 0.29, below the 0.3 cut-off, so the bulletin is discarded. Five of the six also hit the 120 s limit because the second search is another symptom query. |
| Writer ignores or misuses evidence (4) | `doc_kb_range_shortfall_faq`, `prm_undocumented_overclaimed`, `syn_sales_south_q2_vs_q3`, `syn_sales_flagship_avg_price_2026` | The retrieved chunk contains "field report" but the answer says "contact service"; SB-114 applied to a vehicle whose payload is normal; the SQL result had no region column so the writer said it could not compare the south; ₹136,961 rounded to ₹137,000 and failed by the judge. |
| Wrong tool (1) | `doc_sales_q3_2026_midquarter` | SQL used although the Y2 rule sends this to documents; the ten weeks were not counted. |
| Retrieval, other cause (1) | `adv_longest_range` | The search found the FAQ, not the table with "EVX Max 240 km". |

SQL is no longer the problem: the weighted baseline is correct (V-007: 20.9 A against the reference 21.6 A) and only one query was rejected.

Direct retrieval tests (my own queries, not the planner's): "range estimate dropped this week on vehicle V-042" scores 0.03; adding "overload payload current draw" retrieves SB-114 at 0.53. "vehicle takes longer than usual to charge" scores 0.287; "charger low power charging takes longer" retrieves SB-140 at 0.92. Adding "specification table" to the range query retrieves the owner manual at 0.97.

## Conclusion

Y1 is correct and stays. Y2 gave one fix and was ignored on another case. Fix Y does not raise the pass rate on its own because the bottleneck moved to retrieval query wording and the writer. Next fix (Z): build the retrieval query from the metrics the SQL found abnormal, and search after measuring.
