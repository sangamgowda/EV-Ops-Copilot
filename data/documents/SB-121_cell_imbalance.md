---
title: SB-121 — Cell imbalance and gradual capacity loss
doc_type: service_bulletin
domain: diagnostic
applies_to_models: [SC-F50G1]
effective_date: 2025-11-20
---

# SB-121 — Cell imbalance and gradual capacity loss

## Summary

A small number of previous-generation 5 kWh packs develop cell
imbalance after heavy fast-cycling. Cell health falls steadily over
several weeks and usable range drops with it.

## Symptoms

- Cell health falling by more than 1% per week, typically from the
  low 90s into the mid 80s.
- Range estimate falling in step with cell health.
- Pack voltage sagging under load more than usual.
- Current draw stays close to baseline.
- Trouble codes ERR_402 and, below 85%, ERR_403.

## How to tell it apart from overload

Overload raises current draw with normal cell health. Cell imbalance
lowers cell health with normal current draw. Check both before
deciding.

## Action

1. Run a full balance charge (0–100% on the standard charger).
2. Re-check cell health after 48 hours.
3. If still below 85%, book a pack capacity test. Packs under
   warranty are replaced.
