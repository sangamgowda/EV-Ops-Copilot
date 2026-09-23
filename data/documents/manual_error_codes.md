---
title: Service Manual — Diagnostic Trouble Codes
doc_type: manual
domain: diagnostic
applies_to_models: [SC-F50, SC-F45, SC-F50G1]
effective_date: 2026-01-15
---

# Service Manual — Diagnostic Trouble Codes

This chapter lists every trouble code the vehicle can raise, which
subsystem raises it, and the first action a technician should take.
Codes are shown on the dashboard and stored in the vehicle log.

## Trouble code table

| Code | Subsystem | Meaning | Recommended action | Severity |
|---|---|---|---|---|
| ERR_101 | Motor | Motor over-temperature | Let the motor cool for 20 minutes; check airflow and payload | warning |
| ERR_102 | Motor | Phase current imbalance | Inspect motor phase connectors for corrosion or looseness | critical |
| ERR_201 | Throttle | Throttle signal out of range | Recalibrate throttle; check throttle harness | warning |
| ERR_202 | Throttle | Throttle not returning to zero | Update firmware to 2.6.1 or later; inspect return spring (see SB-127) | critical |
| ERR_301 | Charger | Charger communication lost | Reseat charger plug; try a different socket | info |
| ERR_302 | Charger | Charging current limited by temperature | Charge in shade; allow pack to cool | info |
| ERR_401 | BMS | BMS communication timeout | Reseat BMS connector; update firmware | warning |
| ERR_402 | BMS | Cell voltage imbalance above limit | Run a full balance charge; check cell health (see SB-121) | warning |
| ERR_403 | BMS | Cell health below 85% | Schedule a pack capacity test (see SB-121) | critical |
| ERR_404 | BMS | Pack over-temperature | Stop riding; let pack cool before charging | critical |
| ERR_501 | Brakes | Brake pad wear sensor triggered | Replace front brake pads (see SB-130) | warning |
| ERR_601 | Controller | Sustained over-current | Check payload against rating (see SB-114) | warning |
| ERR_701 | Dashboard | Dashboard software fault | Reboot vehicle; update dashboard firmware | info |
| ERR_801 | Chassis | Load above rated payload detected | Reduce load to within rated payload (see SB-114) | warning |

## Reading the log

Each code is stored with a timestamp and the drive mode active at
the time. Repeated warning codes within 7 days should be treated as
one open issue rather than separate faults.

## Severity levels

- **info** — no action needed while riding.
- **warning** — book a service visit within two weeks.
- **critical** — stop riding and contact service.
