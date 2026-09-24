---
title: SB-135 — Firmware 3.2.0 limits Sonic and Sonic X to 45 km/h
doc_type: service_bulletin
domain: diagnostic
applies_to_models: [Volt 1 Ultra]
effective_date: 2026-09-20
---

# SB-135 — Firmware 3.2.0 limits Sonic and Sonic X to 45 km/h

## Summary

Firmware 3.2.0 was pushed over the air to a small group of Volt 1
Ultra scooters. A fault in its ride-mode table applies the Eco X
speed limit (45 km/h) to Sonic and Sonic X. The scooter accelerates
normally but will not go past about 45 km/h in those two modes. Eco X,
Eco, Ride and Air are not affected.

## Symptoms

- Rider reports the scooter "will not go above 45" in Sonic or
  Sonic X.
- Average speed in Sonic and Sonic X is 30–40% below the model
  baseline (62 and 70 km/h), clustered around 40–44 km/h.
- Current draw in those modes is lower than usual, because the
  motor is being held back, not overworked.
- Trouble code ERR_205 (ride-mode speed limit mismatch).
- Payload, cell health and charging are normal.

## How to confirm

1. Check the firmware version in the vehicle configuration.
2. Compare speed in Sonic and Sonic X since the update date with the
   baseline for the model and mode.
3. Confirm speed in Ride and Air is normal. If every mode is slow,
   look elsewhere (brakes dragging, overload).

## Action

- Update to firmware 3.2.1 over the air or at a service centre. The
  correct speed limits return immediately; no parts are needed.
- Vehicles still on 3.1.4 are not affected and should skip 3.2.0.
