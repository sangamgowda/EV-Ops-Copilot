# X + Y + Z - evaluation record (full run)

Run `20261123-145910`, commit `334ea51` (fix X `2ccf3bc` + fix Y `43d2cfd` + fix Z `e86b0e8`; later commits only add records). One full run of all 54 cases, free Groq keys (stopped seven times by the daily token limit and resumed on another key; one log in `logs/result_XYZ_test_log.txt`), same exam, date (2026-10-02), 120 s limit and models as A' and B.

## Result against A' and B

| | A' run 1 | A' run 2 | B (X) | **X+Y+Z** |
|---|---|---|---|---|
| Pass rate, 46 non-trap cases | 56.5% (26), 42.2 - 69.8 | 60.9% (28), 46.5 - 73.6 | 52.2% (24), 38.1 - 65.9 | **65.2% (30), 50.8 - 77.3** |
| Abstention traps | 8 of 8 | 7 of 8 | 8 of 8 | **7 of 8** |
| Failed non-trap cases | 20 | 18 | 22 | **16** |
| Ended on the 120 s limit (all 54 cases) | 17 | 16 | 14 | **11** |
| Failed evidence (rejected SQL) | 20 | 23 | 5 | **4** |
| Mean seconds per non-trap case | 57.6 | 48.7 | 49.3 | **44.8** |

A' = 58.7% (mean of its two runs). X+Y+Z = 65.2%, i.e. **+6.5 points** over A' and +13.0 points over B. The intervals overlap, so this is an improvement that is not proven at this sample size. The result does not separate the three fixes; each fix has only an 18-case check (`fix_x_check_vs_A_prime.md`, `fix_y_check_vs_A_prime_and_B.md`, `fix_z_check_vs_Y_A_prime_B.md`). The timeout counts here are stop reason "timeout" over all 54 cases and are higher than the earlier records, which counted differently.

Against A' (non-trap): 6 failed in both A' runs and pass now (`syn_overload_V-007`, `syn_charger_V-091`, `syn_cell_wear_V-036`, `doc_sales_q3_2026_midquarter`, `adv_holdout_partner_discount`, `prm_nonbreaking_hyphen`); 4 passed in both A' runs and fail now (`syn_code_ERR_303`, `syn_sales_flagship_avg_price_2026`, `doc_SB-121_removable_pack_cell_wear`, `prm_ride_mode_limits`).
Against B: 10 fixed, 4 broken (`syn_speed_cap_V-088`, `syn_code_ERR_303`, `doc_sales_q2_2026_review`, `doc_SB-121_removable_pack_cell_wear`).
Trap failure: `trap_cell_chemistry` (answered when it should have abstained).

## What each fix did

- **X, SQL rules (prompt v6):** rejected SQL fell from about 21 to 5 but the pass rate fell from 58.7% to 52.2%, because its baseline template used `max(b.nominal_value)`, which is wrong for modes with different baselines (current draw: Saver 11.7, Normal 23.1, Sport 32.9, Sport+ 43.1), and because it left the currency and wrong-source problems.
- **Y, weighted baseline and document rule (prompt v7, synthesize v4):** the baseline is now the reading-weighted average and terms and limits go to documents, prices in rupees. On its 18-case check: 4 to 6. The cause was still not found, because retrieval discarded the bulletin.
- **Z, retrieval query names the cause (prompt v8):** symptom-worded searches scored 0.03 - 0.29, under the 0.3 cut-off; adding the causes and the measured metrics retrieves SB-114 at 0.53 and SB-140 at 0.92. On its 18-case check: 6 to 9, and timeouts on those cases from 5 to 2.

## The 16 non-trap cases that still fail

| Cause | Cases |
|---|---|
| Hit the 120 s limit (7) | `doc_SB-114_sustained_overload`, `doc_SB-140_charger_low_power`, `adv_refute_claim`, `adv_ambiguous`, `adv_longest_range`, `prm_charger_doc_missed`, `prm_battery_doc_missed` |
| Wrong source: SQL used for a rule or spec (2) | `prm_ride_mode_limits` (typical speeds 32 - 70 km/h instead of the manual's 45 - 115), `doc_sales_q2_2026_review` (invented "36 new showrooms"; reference 38 to 52 outlets) |
| Writer drops or invents facts (5) | `syn_code_ERR_303` (no recommended action), `doc_SB-121_removable_pack_cell_wear` (asserts ERR_403, skips the balance-charge steps), `adv_holdout_brakes` (no SB-130 part, no regenerative braking), `syn_speed_cap_V-088` (no 42 against 66 km/h), `doc_kb_range_shortfall_faq` ("contact service" instead of "field report") |
| Judge or rounding (1) | `syn_sales_flagship_avg_price_2026` (₹137,000 instead of ₹136,961) |
| Trap answered as if known (1) | `prm_undocumented_overclaimed` |

## Reading for the resume line

Pass rate 58.7% to 65.2% (+6.5 points) on 46 cases after X, Y and Z together, within noise; rejected SQL from about 21 to 4. Fix X alone lowered the pass rate (58.7% to 52.2%) and must not be claimed as the cause of the gain. Held-out run (Phase 5) not done yet.
