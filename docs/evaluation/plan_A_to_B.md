# EV Ops Copilot: Evaluation Plan (A' → Fix X → B)

**Goal:** a fair baseline A', one fix X, and a fair B, so the improvement is real.

> Written 2026-11-21, before A' was measured. Phase 0 steps 0.1 and 0.2 (paid key, raised queue limit) were not done: every run used free Groq keys, so waiting for the token budget is part of every result. Phase 5 became the held-out check (`plan_heldout.md`, `result_heldout.md`).

**Out of scope for now:** latency and token reduction (parked).

## The idea in one picture

```
Fair test setup  →  A' (old code)  →  find biggest failure  →  fix X  →  B (old code + fix X)
                     same exam, same environment, same rules  ──────────────────────────►
```

The only difference between A' and B is **fix X**. Everything else stays identical.

## Why A has to be re-measured

The current A (56.5%) was measured on the Groq free tier. The throttle caused about 13 timeouts, and many of those were the agent waiting for token budget, not giving a wrong answer. If B runs without the throttle, it will look better even with no real improvement. A and B must run in the **same environment**, so A is re-measured once as **A'**.

---

## Phase 0: Make the test fair (start here)

| Step | What to do | Done when |
|---|---|---|
| 0.1 | Get a **paid Groq key**, with more than 8K tokens per minute | Key works in preflight |
| 0.2 | Raise the app's own queue limit (`llm.queue`) in the **eval config** to match the new key | No more waiting for budget |
| 0.3 | Fix the **two exam errors** (`adv_cross_domain`, `adv_south_outperformed`), then **freeze the golden set** | Golden set not touched again until the end |
| 0.4 | Keep the **120 s limit** and the **pinned date (2026-10-02)** | Same for every run |

**First action:** steps 0.1 and 0.2.

---

## Phase 1: Measure A' (the fair baseline)

1. Create a new branch from **`1dcc428`**. That's the A code: date pin included, no SQL fix yet.
2. Add the exam fix commit from step 0.3.
3. Run **all 54 cases, twice**.
4. Write down the **A' pass rate** (on the 46 non-trap cases) and save the per-case results.

✅ **Done when:** two complete runs, no budget stops, very few or no timeouts.

---

## Phase 2: Choose fix X

1. Read every failed case and note **where it first went wrong**.
2. Sort the failures into buckets and count them:
   - Rejected SQL
   - Wrong source (used SQL when the answer was in a document)
   - Wrong number, unit or currency
   - Missed a document instruction
3. **Fix X = the biggest bucket** that you can fix cleanly.

Most likely this is the **SQL rules (prompt v6)**, which are already built and validated. But let the A' data confirm it.

✅ **Done when:** you can say, "Fix X targets N cases, because…"

---

## Phase 3: Apply fix X

1. Add fix X to the A' branch as **one commit**. If it's the SQL rules, bring over the v5/v6 prompt changes as one package.
2. Quick check: run **only the target cases plus about 5 cases that already pass**.
3. If targets improve and the passing cases don't break, move on. If not, adjust and re-check.

✅ **Done when:** targets improve, nothing that used to pass breaks.

---

## Phase 4: Measure B

1. Run **all 54 cases, twice**, on the A' + fix X code, in the same environment.
2. Compare with A':
   - pass rate A' → B
   - **regression diff:** what got fixed and what broke
   - traps still 8 of 8?

✅ **Done when:** you have both numbers and the case-by-case diff.

---

## Phase 5: Confirm and write up

1. Run the **48 held-out cases** once on A' code and once on B code.
2. Write the result line:

> "Pass rate **A'% → B%** (+N points) on 46 golden cases, held-out **X% → Y%**, after **[fix X]**."

---

## The rules, in short

- **One fix only** between A' and B.
- **Same everything else:** key, queue limit, exam, date and time limit.
- **Run twice** each time, because 46 cases are noisy (about ±14 points).
- Report in **points** (56 → 66 is +10 points).
- If B doesn't beat A', report the honest result instead, for example: "rejected SQL 14 → 3, pass rate unchanged".

## Order

Phase 0 (fair test) → Phase 1 (A') → Phase 2 (choose fix) → Phase 3 (apply fix) → Phase 4 (B) → Phase 5 (held-out + write-up)
