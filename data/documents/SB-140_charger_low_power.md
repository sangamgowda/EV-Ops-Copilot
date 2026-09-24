---
title: SB-140 — Standard charger delivering under half its rated power
doc_type: service_bulletin
domain: diagnostic
applies_to_models: [Volt 1, Volt 1 Gen 2, Volt 1 Ultra]
effective_date: 2026-09-05
---

# SB-140 — Standard charger delivering under half its rated power

## Summary

Scooter taking too long to charge? Slow charging, low charging power,
or a battery that is not full in the morning after charging overnight
is most often a faulty charger, not a faulty battery.

A batch of 750 W standard chargers can develop a failing output
stage. The charger still works, so owners rarely notice at first, but
it delivers only 300–400 W. A top-up that normally takes about 4
hours then takes 8 or more, and owners report that the scooter is not
full in the morning.

## Symptoms

- Charging power readings of 0.30–0.40 kW against the 0.75 kW
  baseline — a drop of more than 45%.
- Charge sessions running past midnight into the morning.
- Riding metrics (current draw, speed, cell health) all normal: the
  battery and motor are fine.
- Trouble code ERR_303 (charger output below rating).

## How to tell it apart from heat derating

When the pack is hot, the scooter reduces charging current on purpose
and logs ERR_302. That drop is temporary and recovers once the pack
cools. A failing charger stays low every night, whatever the
temperature.

## Action

1. Test the charger on a second scooter. If power is still low, the
   charger is at fault, not the vehicle.
2. Replace the charger under its 1-year / 10,000 km warranty.
3. Charging power returns to about 0.75 kW immediately.
