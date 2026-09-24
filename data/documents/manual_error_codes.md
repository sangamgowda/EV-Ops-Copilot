---
title: Service Manual — Diagnostic Trouble Codes
doc_type: manual
domain: diagnostic
applies_to_models: [Volt 1, Volt 1 Gen 2, Volt 1 Ultra]
effective_date: 2026-09-01
---

# Service Manual — Diagnostic Trouble Codes

This chapter lists every trouble code a Volt 1 scooter can raise,
which subsystem raises it, and the first action a technician should
take. Codes are shown on the dashboard and stored in the vehicle log.

## Trouble code table

| Code | Subsystem | Meaning | Recommended action | Severity |
|---|---|---|---|---|
| ERR_101 | Motor | Motor over-temperature | Let the motor cool for 20 minutes; check airflow and payload | warning |
| ERR_102 | Motor | Phase current imbalance | Inspect motor phase connectors for corrosion or looseness | critical |
| ERR_201 | Throttle | Throttle signal out of range | Recalibrate throttle; check throttle harness | warning |
| ERR_205 | Controller | Ride-mode speed limit mismatch | Check firmware version; update 3.2.0 to 3.2.1 (see SB-135) | warning |
| ERR_301 | Charger | Charger communication lost | Reseat charger plug; try a different socket | info |
| ERR_302 | Charger | Charging current limited by temperature | Charge in shade; allow pack to cool | info |
| ERR_303 | Charger | Charger output below rating | Test charger on another scooter; replace if still low (see SB-140) | warning |
| ERR_401 | BMS | BMS communication timeout | Reseat BMS connector; update firmware | warning |
| ERR_402 | BMS | Cell voltage imbalance above limit | Run a full balance charge; check cell health (see SB-121) | warning |
| ERR_403 | BMS | Cell health below 85% | Schedule a pack capacity test (see SB-121) | critical |
| ERR_404 | BMS | Pack over-temperature | Stop riding; let pack cool before charging | critical |
| ERR_405 | BMS | Removable pack not latched | Clean and adjust the pack dock latch | warning |
| ERR_501 | Brakes | Brake pad wear sensor triggered | Replace front brake pads (see SB-130) | warning |
| ERR_601 | Controller | Sustained over-current | Check payload against rating (see SB-114) | warning |
| ERR_701 | Dashboard | Dashboard software fault | Reboot vehicle; update dashboard firmware | info |
| ERR_801 | Chassis | Load above rated payload detected | Reduce load to within rated payload (see SB-114) | warning |

## Reading the log

Each code is stored with a timestamp and the ride mode active at the
time. Repeated warning codes within 7 days should be treated as one
open issue rather than separate faults.

## Severity levels

- **info** — no action needed while riding.
- **warning** — book a service visit within two weeks.
- **critical** — stop riding and contact service.
