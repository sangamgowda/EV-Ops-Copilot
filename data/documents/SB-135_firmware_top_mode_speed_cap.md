---
title: SB-135 — Firmware 3.2.0 limits Sport+ and Ultra to 45 km/h
doc_type: service_bulletin
domain: diagnostic
applies_to_models: [EVX Max]
effective_date: 2026-09-20
---

# SB-135 — Firmware 3.2.0 limits Sport+ and Ultra to 45 km/h

## Summary

Firmware 3.2.0 was pushed over the air to a small group of EVX Max
scooters. A fault in its ride-mode table applies the Saver speed
limit (45 km/h) to the two highest modes, Sport+ and Ultra. The
scooter accelerates normally but will not go past about 45 km/h in
those two modes. Saver, Eco, Normal and Sport are not affected.

## Symptoms

- Rider reports the scooter "will not go above 45" in Sport+ or
  Ultra.
- Average speed in Sport+ and Ultra is 30–40% below the model
  baseline (62 and 70 km/h), clustered around 40–44 km/h.
- Current draw in those modes is lower than usual, because the
  motor is being held back, not overworked.
- Trouble code ERR_205 (ride-mode speed limit mismatch).
- Payload, cell health and charging are normal.

## How to confirm

1. Check the firmware version in the vehicle configuration.
2. Compare speed in Sport+ and Ultra since the update date with the
   baseline for the model and mode.
3. Confirm that speed in the Normal and Sport modes is as expected. If
   every mode is slow, look elsewhere (brakes dragging, overload).

## Action

- Update to firmware 3.2.1 over the air or at a service centre. The
  correct speed limits return immediately; no parts are needed.
- Vehicles still on 3.1.4 are not affected and should skip 3.2.0.
