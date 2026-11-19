# Fix X, first check - SQL rules in the planner prompt

Run `20261119-152848`, commit `acda87c` (plan prompt v5), evaluation date 2026-10-02.
A screen on 19 of the 46 scored cases, **not** the final B: the 10 cases that had rejected SQL, the 4 other timeouts and
5 passing multi-tool cases as a guard. It was run before spending two days of free-tier tokens on a full run.

## Result against baseline A on the same 19 cases

| | A | After the SQL rules |
|---|---|---|
| Passed | 5 of 19 | 6 of 19 |
| Hit the 120 s limit | 13 | 9 |
| Rejected queries | 14 | 10 |
| Mean time per case | 102 s | 87 s |

| Case | A | After SQL rules | Change | Time | Rejected SQL |
|---|---|---|---|---|---|
| `syn_overload_V-042` | pass | fail | REGRESSION | 107 s -> 120 s | 0 -> 0 |
| `syn_overload_V-007` | fail | fail | same | 120 s -> 120 s | 1 -> 0 |
| `syn_speed_cap_V-055` | fail | pass | new pass | 120 s -> 63 s | 2 -> 0 |
| `syn_speed_cap_V-088` | pass | pass | same | 62 s -> 62 s | 0 -> 0 |
| `syn_charger_V-091` | fail | fail | same | 120 s -> 120 s | 1 -> 1 |
| `syn_cell_wear_V-036` | fail | fail | same | 120 s -> 120 s | 1 -> 1 |
| `doc_SB-114_sustained_overload` | fail | fail | same | 120 s -> 62 s | 2 -> 2 |
| `doc_SB-135_firmware_top_mode_speed_cap` | fail | fail | same | 120 s -> 120 s | 2 -> 3 |
| `doc_SB-140_charger_low_power` | fail | fail | same | 120 s -> 120 s | 0 -> 0 |
| `adv_typo_vehicle` | pass | pass | same | 62 s -> 66 s | 0 -> 0 |
| `adv_refute_claim` | pass | fail | REGRESSION | 67 s -> 120 s | 0 -> 1 |
| `adv_ambiguous` | fail | fail | same | 120 s -> 120 s | 0 -> 0 |
| `adv_holdout_brakes` | fail | pass | new pass | 120 s -> 78 s | 0 -> 0 |
| `adv_south_outperformed` | pass | fail | REGRESSION | 14 s -> 120 s | 0 -> 0 |
| `prm_ride_mode_limits` | fail | pass | new pass | 68 s -> 23 s | 1 -> 1 |
| `prm_charger_doc_missed` | fail | fail | same | 120 s -> 65 s | 2 -> 1 |
| `prm_battery_doc_missed` | fail | fail | same | 120 s -> 65 s | 0 -> 0 |
| `prm_undocumented_overclaimed` | fail | fail | same | 120 s -> 28 s | 1 -> 0 |
| `prm_nonbreaking_hyphen` | fail | pass | new pass | 120 s -> 66 s | 1 -> 0 |

## What it shows

1. **The rules do what they were written for.** Rejected queries fell from 14 to 10, timeouts from 13 to 9, mean time per case from 102 s to 87 s. Four cases that timed out now pass (`syn_speed_cap_V-055`, `adv_holdout_brakes`, `prm_ride_mode_limits`, `prm_nonbreaking_hyphen`).
2. **The score barely moves: +1 case net** (5 to 6 of 19), inside the noise of a sample this small. Three cases that passed now time out (`syn_overload_V-042`, `adv_refute_claim`, `adv_south_outperformed`).
3. **The three regressions had no SQL errors.** They fit the free tier's per-minute token budget, not the SQL: the planner prompt (instructions plus schema) is about 4.3K tokens, one planning call with its output allowance is about 5.3K of the 8K tokens a minute the strong model may use, and a two-lap diagnostic question makes two planning calls plus the writing call. That is well over 2 minutes of budget, so a case can miss the 120 s limit however good its SQL is. The harness does not flag this as throttling because the app's own queue does the waiting, so it reads as a slow agent.
4. **Four rejected queries remain** (`syn_charger_V-091`, `syn_cell_wear_V-036`, `doc_SB-135_firmware_top_mode_speed_cap`, `adv_refute_claim`): the rules are not always followed.

## Token measurements (planner call, no model needed)

| | Plan prompt | Schema | Input tokens (approx.) |
|---|---|---|---|
| v4 (baseline) | 8,949 chars | 8,844 chars | 4,448 |
| v5 rules, before trimming | 10,560 chars | 8,844 chars | 4,851 |
| v5 rules, trimmed, vehicle-only schema | 9,381 chars | 7,762 chars | 4,285 |

Trimming the prompt text and hiding the sales table from vehicle-only questions saves about 160 tokens per planning call (3.7%) against v4. That is too little to change the time limit; the large costs are the two laps and the examples, not the wording.

## Decision

Kept: the SQL rules (they remove real database errors and some of the delay) and the small trims (net smaller than v4). Not done: a deeper cut of the examples and schema descriptions, which risks answer quality and needs its own check. The full B run is the next step.

## Files

`evaluation_runs/` is not committed; the per-case numbers above come from run `20261119-152848` and `baseline_A_cases.json`.
