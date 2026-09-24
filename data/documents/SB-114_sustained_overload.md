---
title: SB-114 — Sustained overload raises drive current and reduces range
doc_type: service_bulletin
domain: diagnostic
applies_to_models: [Volt 1, Volt 1 Gen 2, Volt 1 Ultra]
effective_date: 2026-02-10
---

# SB-114 — Sustained overload raises drive current and reduces range

## Summary

Riding regularly with a total load above the rated payload makes the
motor draw more current for the same speed. Over several days this
shows up as higher average current draw, higher motor temperature
and a lower range estimate. Battery cell health is not affected.

## Symptoms

- Average current draw 25–40% above the model baseline in the same
  ride mode (most visible in Ride and Air).
- Range estimate 20–30% below normal.
- Trouble codes ERR_601 (sustained over-current) or ERR_801 (load
  above rated payload) may be logged.
- Cell health stays in the normal band (above 95%).

## Cause

Rated payload on every Volt 1 model is 150 kg (rider, pillion and
cargo). Above this, the controller supplies more current to hold
speed, particularly on inclines and when pulling away. Energy use per
kilometre rises roughly in line with the excess load.

## How to confirm

1. Compare 7-day average current draw with the baseline for the same
   model and ride mode.
2. Check recorded payload readings for the same period.
3. Check cell health. If it is normal, battery wear is ruled out and
   overload is the likely cause.

## Action

- Advise the rider to keep total load within the rated payload.
- No part replacement needed. Range recovers once load is reduced.
- For fleet operators, review how loads are assigned per vehicle.
