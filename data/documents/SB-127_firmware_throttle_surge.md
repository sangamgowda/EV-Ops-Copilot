---
title: SB-127 — Firmware 2.6.0 throttle surge and motor temperature
doc_type: service_bulletin
domain: diagnostic
applies_to_models: [SC-F50, SC-F45]
effective_date: 2026-08-28
---

# SB-127 — Firmware 2.6.0 throttle surge and motor temperature

## Summary

Firmware 2.6.0 changed the throttle response map. On some vehicles
the motor keeps driving for about one second after the throttle is
released, and motor temperature in sport mode runs 10–15 °C higher
than on 2.5.x.

## Symptoms

- Rider reports the scooter surging forward after letting go of the
  throttle.
- Motor temperature above baseline in sport mode only.
- Trouble code ERR_202 may be logged.

## Affected versions

| Firmware | Status |
|---|---|
| 2.5.x | Not affected |
| 2.6.0 | Affected |
| 2.6.1 | Fixed |

## Action

Update to firmware 2.6.1 over the air or at a service centre. No
hardware change is required.
