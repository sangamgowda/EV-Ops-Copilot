# Fix Z - check record (18 cases)

Run `20261123-141245`, commit `e86b0e8` (fix Y `43d2cfd` + fix Z, plan prompt v8). Same 18 cases as the fix Y check, same exam, date (2026-10-02), 120 s limit and models; free Groq keys, stopped once by the daily limit and resumed (logs joined in `logs/fix_z_check_test_log.txt`).

Fix Z: the retrieval query names the cause. Lap 1: the symptom plus the causes the documents may give, as possibilities. Lap 2+: the symptom plus what was measured. Specification questions name the table. Prompt only, no code change.

## Result on the same 18 cases

| | A' run 1 | A' run 2 | B | Y | **Z** |
|---|---|---|---|---|---|
| Pass | 7 | 7 | 4 | 6 | **9** |
| Hit the 120 s limit | 8 | 5 | 3 | 5 | **2** |

Fixed against Y: `syn_overload_V-042`, `adv_typo_vehicle`, `syn_sales_south_q2_vs_q3`. Nothing that passed in Y broke.
Still failing (9): `syn_overload_V-007`, `syn_charger_V-091`, `prm_nonbreaking_hyphen`, `prm_undocumented_overclaimed`, `prm_charger_doc_missed`, `doc_sales_q3_2026_midquarter`, `syn_sales_flagship_avg_price_2026`, `doc_kb_range_shortfall_faq`, `adv_longest_range`.

Honest reading: +2 cases over A' on 18 cases, inside the noise of a check this small. It is the best of the four configurations on these cases, but it is not yet a measured pass-rate gain.

## What worked and what is left

The retrieval target worked: in all six overload and charger cases the search now returns SB-114 or SB-140 (evidence `ok`, not `below_threshold`), and timeouts on these cases fell from 5 to 2 because lap 2 no longer repeats a symptom query that finds nothing. Only two of the six now pass; the other four fail on the answer, not on retrieval:

- Baseline precision and meaning: the template rounds to one decimal, so the charger baseline 0.75 kW shows as 0.8 kW (`syn_charger_V-091`, `prm_charger_doc_missed`); and the payload "baseline" is the spec value 95 kg while the rated load in the documents is 150 kg, so the answer says "98.6% above 95 kg" where the reference says "190 kg against a 150 kg rating" (`syn_overload_V-007`, `prm_nonbreaking_hyphen`).
- The writer leaves out the recommended action even when SB-140 is in the evidence ("replace the charger"), and leaves out the vehicle id (`syn_charger_V-091`).
- The trap `prm_undocumented_overclaimed` still names overload as the cause although payload is normal (the lap-1 query lists possible causes; the writer treats the match as a diagnosis).
- Unchanged by fix Z: `doc_sales_q3_2026_midquarter` (SQL instead of documents), `doc_kb_range_shortfall_faq` (retrieved text has "field report", answer says "contact service"), `syn_sales_flagship_avg_price_2026` (rounded price), `adv_longest_range` (timeout; lap 1 query still below threshold at 0.088).
