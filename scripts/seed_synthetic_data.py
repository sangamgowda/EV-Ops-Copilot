"""Seed a realistic synthetic dataset into Postgres.

Because the data is generated here, ground truth is KNOWN — which is
what makes most of the golden set possible without hand-labelling
every case. The planted scenarios and their measured effect are
written to data/seed_manifest.json.

Model specs, sales volumes, regions and the owner-reported issue
vocabulary come from data/reference/ev_models.yaml. Error codes are
read from the trouble-code table in data/documents/manual_error_codes.md.

What gets seeded:

  vehicles            N vehicles (V-001, V-002, ...) across the three
                      Volt 1 models and several cities, private and
                      fleet usage
  baselines           nominal values per model, ride mode and metric
  telemetry           trip-based readings every few minutes for D days,
                      plus a nightly charging session
  planted scenarios   vehicles with a deliberate, explainable story:
                        overload       payload above rating, current
                                       draw up, cell health normal
                        cell_wear      removable pack wearing: cell
                                       health falling, current normal
                        speed_cap      firmware 3.2.0 holds Sonic and
                                       Sonic X to 45 km/h
                        charger_fault  charger delivers under half its
                                       rated power
                        undocumented   current draw up for no reason
                                       any document explains
  service_events      12 months of scheduled visits and complaints
  sales               24 months, following the monthly curve; the
                      Ultra launches in the south first
  error_codes         parsed from the service manual's code table

Usage:
  python scripts/seed_synthetic_data.py --vehicles 120 --days 90 --reset
  python scripts/seed_synthetic_data.py --vehicles 50 --days 30 --reset
  python scripts/seed_synthetic_data.py --csv-dir data/processed --no-db
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data" / "reference" / "ev_models.yaml"
DOCS_DIR = ROOT / "data" / "documents"
ERROR_CODE_DOC = DOCS_DIR / "manual_error_codes.md"
MANIFEST = ROOT / "data" / "seed_manifest.json"

IST = timezone(timedelta(hours=5, minutes=30))
FLEET_MODELS = ("Volt 1", "Volt 1 Gen 2", "Volt 1 Ultra")
# Readings taken while plugged in carry this in place of a ride mode,
# so charging has a baseline row like everything else.
CHARGING = "Charging"

UNITS = {
    "charge_power": "kW",
    "current_draw": "A",
    "pack_voltage": "V",
    "payload": "kg",
    "speed": "kmph",
    "ambient_temp": "C",
    "state_of_charge": "pct",
    "range_estimate": "km",
    "motor_temp": "C",
    "cell_health": "pct",
}

# Vehicle numbers that carry a planted story. V-042 is the README
# example. With a smaller --vehicles, numbers wrap around (55 of 50 is
# V-005), so the stories survive but land on different ids than
# docs/DATA_GUIDE.md lists; the manifest always has the real ones.
PLANTED = {
    "overload": [42, 7, 29],
    "cell_wear": [17, 36],
    "speed_cap": [12, 55, 88],
    "charger_fault": [64, 91],
    "undocumented": [23],
}
SCENARIO_MODEL = {
    "cell_wear": "Volt 1",
    "speed_cap": "Volt 1 Ultra",
    "charger_fault": "Volt 1 Gen 2",
    "undocumented": "Volt 1",
}
FIRMWARE_OLD, FIRMWARE_BAD, FIRMWARE_FIXED = "3.1.4", "3.2.0", "3.2.1"
# Modes firmware 3.2.0 wrongly holds to the Eco X limit.
CAPPED_MODES = ("Sonic", "Sonic X")
STANDARD_CHARGER_KW = 0.75

ISSUE_CODES = {
    "Real-world range well below claimed range": [None, None, "ERR_601"],
    "Front brake feels weak; pads wearing quickly": ["ERR_501"],
    "Noise from front fork over bumps": [None],
    "Ride mode only changes after stopping and restarting": ["ERR_701", None],
    "Start switch intermittently unresponsive": [None],
    "Charging slower than expected": ["ERR_302", "ERR_301"],
    "Dashboard freezes or reboots": ["ERR_701"],
    "Rattle from body panels": [None],
    "Removable battery hard to lock into its dock": ["ERR_405", None],
}
ISSUE_RESOLUTIONS = {
    "Real-world range well below claimed range": "Checked tyre pressure and cell health; advised on ride mode",
    "Front brake feels weak; pads wearing quickly": "Front brake pads replaced",
    "Noise from front fork over bumps": "Fork bushes lubricated",
    "Ride mode only changes after stopping and restarting": "Explained mode-change behaviour; dashboard firmware updated",
    "Start switch intermittently unresponsive": "Start switch connector reseated",
    "Charging slower than expected": "Charger and socket tested; within spec",
    "Dashboard freezes or reboots": "Dashboard firmware updated",
    "Rattle from body panels": "Panel clips replaced",
    "Removable battery hard to lock into its dock": "Dock latch cleaned and adjusted",
}

# How often each kind of rider uses each mode. A model's missing
# modes (Sonic X outside the Ultra) are dropped when a rider is built.
FLEET_MODE_WEIGHTS = {"Eco X": 0.10, "Eco": 0.25, "Ride": 0.45, "Air": 0.15, "Sonic": 0.05, "Sonic X": 0.0}
PRIVATE_MODE_WEIGHTS = [
    {"Eco X": 0.15, "Eco": 0.30, "Ride": 0.35, "Air": 0.15, "Sonic": 0.05, "Sonic X": 0.0},
    {"Eco X": 0.05, "Eco": 0.15, "Ride": 0.40, "Air": 0.25, "Sonic": 0.10, "Sonic X": 0.05},
    {"Eco X": 0.0, "Eco": 0.05, "Ride": 0.25, "Air": 0.30, "Sonic": 0.25, "Sonic X": 0.15},
]
MOTOR_TEMP_C = {"Eco X": 40.0, "Eco": 42.0, "Ride": 48.0, "Air": 54.0, "Sonic": 60.0, "Sonic X": 66.0}


# ── reference data ───────────────────────────────────────────


def load_reference() -> dict[str, Any]:
    with open(REFERENCE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_error_codes(path: Path) -> list[dict[str, str]]:
    """Read the trouble-code table out of the service manual.

    Stands in for ingestion-time promotion until rag/ingest.py is
    built; both write the same rows.
    """
    rows: list[dict[str, str]] = []
    header: Optional[list[str]] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rec = dict(zip(header, cells))
        if re.fullmatch(r"ERR_\d+", rec.get("code", "")):
            rows.append({
                "code": rec["code"],
                "subsystem": rec["subsystem"],
                "meaning": rec["meaning"],
                "recommended_action": rec["recommended action"],
                "severity": rec["severity"],
                "source_document": path.stem,
            })
    return rows


# ── model physics ────────────────────────────────────────────


def baseline_current(spec: dict[str, Any], mode: str) -> float:
    return spec["wh_per_km"][mode] * spec["avg_speed_kmph"][mode] / spec["pack_nominal_v"]


def full_range_km(spec: dict[str, Any], mode: str, health: float = 97.0) -> float:
    usable_wh = spec["battery_kwh"] * 1000 * 0.95 * health / 100
    return usable_wh / spec["wh_per_km"][mode]


def baseline_motor_temp(mode: str) -> float:
    return MOTOR_TEMP_C[mode]


def build_baselines(ref: dict[str, Any]) -> list[tuple]:
    rated = ref["common"]["rated_payload_kg"]
    rows = []
    for code in FLEET_MODELS:
        spec = ref["models"][code]
        for mode in spec["modes"]:
            rows += [
                (code, mode, "current_draw", round(baseline_current(spec, mode), 1), 10.0, rated),
                (code, mode, "speed", float(spec["avg_speed_kmph"][mode]), 15.0, rated),
                (code, mode, "range_estimate", round(full_range_km(spec, mode), 0), 10.0, rated),
                (code, mode, "motor_temp", baseline_motor_temp(mode), 15.0, rated),
                (code, mode, "pack_voltage", spec["pack_nominal_v"], 8.0, rated),
                (code, mode, "cell_health", 97.0, 3.0, rated),
                (code, mode, "payload", 95.0, 60.0, rated),
            ]
        rows.append((code, CHARGING, "charge_power", STANDARD_CHARGER_KW, 10.0, rated))
    return rows


# ── vehicles ─────────────────────────────────────────────────


@dataclass
class Vehicle:
    vehicle_id: str
    model_code: str
    city: str
    zone: str
    usage: str                       # private | fleet
    sold_on: date
    manufactured_on: date
    firmware: str
    firmware_updated_on: Optional[date]
    mode_weights: dict[str, float]
    health_start: float
    scenario: Optional[str] = None
    onset: Optional[date] = None
    odometer_km: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    # Data-quality flaws, assigned by add_real_world_flaws().
    offline_days: set[date] = field(default_factory=set)
    reports_odometer: bool = True
    reports_region: bool = True


def vin(n: int, total: int) -> str:
    # Short on purpose, with a dash: grounding reads "V-042" as a name,
    # where a bare "V042" would have its digits checked as a claim.
    return f"V-{((n - 1) % total) + 1:03d}"


def weighted(rng: random.Random, items: dict[str, float]) -> str:
    keys = list(items)
    return rng.choices(keys, weights=[items[k] for k in keys])[0]


def build_vehicles(ref: dict[str, Any], n: int, start: date, end: date, rng: random.Random) -> list[Vehicle]:
    models = ref["models"]
    regions = ref["regions"]

    scenario_of: dict[str, str] = {}
    for scenario, numbers in PLANTED.items():
        for num in numbers:
            scenario_of.setdefault(vin(num, n), scenario)

    vehicles = []
    for i in range(1, n + 1):
        vid = vin(i, n)
        scenario = scenario_of.get(vid)
        if scenario == "overload":
            model = "Volt 1 Gen 2" if vid == "V-042" else rng.choice(["Volt 1", "Volt 1 Gen 2"])
        elif scenario in SCENARIO_MODEL:
            model = SCENARIO_MODEL[scenario]
        else:
            model = weighted(rng, {m: models[m]["fleet_share"] for m in FLEET_MODELS})
        spec = models[model]

        usage = "fleet" if (scenario == "overload" or rng.random() < 0.3) else "private"

        sold_from = date.fromisoformat(spec["sold_from"])
        sold_until = min(date.fromisoformat(spec.get("sold_until", end.isoformat())), start - timedelta(days=10))
        span = max((sold_until - sold_from).days, 1)
        sold_on = sold_from + timedelta(days=rng.randrange(span))
        manufactured_on = sold_on - timedelta(days=rng.randint(12, 60))

        zone = weighted(rng, {z: r["share"] for z, r in regions.items()})
        elsewhere = spec.get("launched_elsewhere")
        if elsewhere and sold_on < date.fromisoformat(elsewhere):
            zone = "south"                    # only sold there before
        city = rng.choice(regions[zone]["cities"])

        # Firmware 3.2.0 reaches only the planted vehicles, so every
        # vehicle on it tells the same story.
        firmware, fw_date = FIRMWARE_OLD, None
        if scenario == "speed_cap":
            firmware, fw_date = FIRMWARE_BAD, end - timedelta(days=10)
        elif scenario is None and model != "Volt 1" and rng.random() < 0.5:
            firmware, fw_date = FIRMWARE_FIXED, end - timedelta(days=rng.randint(1, 5))

        base = FLEET_MODE_WEIGHTS if usage == "fleet" else rng.choice(PRIVATE_MODE_WEIGHTS)
        mode_weights = {m: base[m] for m in spec["modes"]}
        if scenario == "speed_cap":
            # Riders who use the fast modes, or the cap never shows.
            mode_weights = {m: w for m, w in PRIVATE_MODE_WEIGHTS[2].items() if m in spec["modes"]}

        age_days = (end - sold_on).days
        health_start = 99.5 - age_days * rng.uniform(0.004, 0.008)
        if scenario == "cell_wear":
            health_start = 92.0

        onset = None
        if scenario in ("overload", "undocumented"):
            onset = end - timedelta(days=9)
        elif scenario == "cell_wear":
            onset = start
        elif scenario == "speed_cap":
            onset = fw_date
        elif scenario == "charger_fault":
            onset = end - timedelta(days=12)

        daily_km = 70 if usage == "fleet" else 28
        odometer = max(age_days - (end - start).days, 0) * daily_km * rng.uniform(0.7, 1.2)

        v = Vehicle(
            vehicle_id=vid, model_code=model, city=city, zone=zone, usage=usage,
            sold_on=sold_on, manufactured_on=manufactured_on, firmware=firmware,
            firmware_updated_on=fw_date, mode_weights=mode_weights,
            health_start=round(health_start, 2), scenario=scenario, onset=onset,
            odometer_km=odometer,
        )
        v.config = {
            "battery_pack": f"{spec['battery_kwh']:g}kWh",
            "firmware_version": firmware,
            "firmware_updated_on": fw_date.isoformat() if fw_date else None,
            "usage": usage,
            "features": {
                "navigation": rng.random() < 0.7,
                "fast_charger": scenario == "charger_fault" or rng.random() < 0.3,
                "removable_pack": True,
            },
        }
        vehicles.append(v)
    return vehicles


# ── telemetry ────────────────────────────────────────────────


def ambient(zone_c: float, when: datetime, rng: random.Random) -> float:
    diurnal = 4.5 * math.sin((when.hour + when.minute / 60 - 9) / 24 * 2 * math.pi)
    return zone_c + diurnal + rng.gauss(0, 0.8)


def cell_health_on(v: Vehicle, day: date, start: date, days: int) -> float:
    elapsed = (day - start).days
    if v.scenario == "cell_wear":
        # ~92 → ~84 across the window whatever its length, so the story
        # (and SB-121's thresholds) hold at 30 days and at 90.
        return v.health_start - elapsed * 8.0 / max(days - 1, 1)
    return v.health_start - elapsed * 0.006


def is_active(v: Vehicle, scenario: str, when: date) -> bool:
    return v.scenario == scenario and v.onset is not None and when >= v.onset


def telemetry_for(
    v: Vehicle, ref: dict[str, Any], start: date, days: int, interval: int, rng: random.Random
) -> Iterable[tuple]:
    spec = ref["models"][v.model_code]
    zone_c = ref["regions"][v.zone]["ambient_c"]
    rated = ref["common"]["rated_payload_kg"]
    cap_wh = spec["battery_kwh"] * 1000 * 0.95
    modes = list(v.mode_weights)
    weights = list(v.mode_weights.values())

    for d in range(days):
        day = start + timedelta(days=d)
        health = cell_health_on(v, day, start, days)
        soc = rng.uniform(88, 100)
        yield (v.vehicle_id, datetime.combine(day, time(6, 0), IST), "cell_health", round(health, 2), None)

        trips = rng.randint(4, 6) if v.usage == "fleet" else rng.randint(1, 3)
        hours = sorted(rng.sample(range(7, 22), trips))
        for hour in hours:
            t = datetime.combine(day, time(hour, rng.randrange(0, 60, 5)), IST)
            mode = rng.choices(modes, weights=weights)[0]
            minutes = rng.randint(20, 60) if v.usage == "fleet" else rng.randint(10, 40)

            if is_active(v, "overload", day):
                payload = rng.uniform(178, 200)
            else:
                payload = min(rng.gauss(92, 18), rated - 5)
            excess = max(0.0, (payload - rated) / rated)
            load_factor = 1 + 1.35 * excess
            fault_factor = 1.30 if is_active(v, "undocumented", day) else 1.0
            capped = is_active(v, "speed_cap", day) and mode in CAPPED_MODES
            wear_sag = max(0.0, 97 - health) * 0.25        # volts lost under load

            yield (v.vehicle_id, t, "payload", round(payload, 1), mode)

            wh_per_km = spec["wh_per_km"][mode] * load_factor * fault_factor
            mode_cap = min(ref["ride_modes"][mode]["speed_cap_kmph"], spec["top_speed_kmph"])
            for step in range(0, minutes, interval):
                ts = t + timedelta(minutes=step)
                amb = ambient(zone_c, ts, rng)
                speed = min(max(rng.gauss(spec["avg_speed_kmph"][mode], 6), 5), mode_cap)
                if capped:
                    # 3.2.0 applies the Eco X limit: the rider holds the
                    # throttle open and the scooter sits just under 45.
                    speed = min(rng.gauss(42, 2), ref["ride_modes"]["Eco X"]["speed_cap_kmph"])
                current = wh_per_km * speed / spec["pack_nominal_v"] * rng.gauss(1, 0.06)
                voltage = spec["pack_nominal_v"] * (0.93 + 0.1 * soc / 100) - wear_sag * rng.uniform(0.8, 1.2)
                motor_t = (
                    amb + (baseline_motor_temp(mode) - 28) + 10 * excess
                    + rng.gauss(0, 1.5)
                )
                km = speed * interval / 60
                soc = max(soc - wh_per_km * km / (cap_wh * health / 100) * 100, 3)
                v.odometer_km += km

                yield (v.vehicle_id, ts, "speed", round(speed, 1), mode)
                yield (v.vehicle_id, ts, "current_draw", round(current, 2), mode)
                yield (v.vehicle_id, ts, "pack_voltage", round(voltage, 2), mode)
                yield (v.vehicle_id, ts, "state_of_charge", round(soc, 1), mode)
                yield (v.vehicle_id, ts, "motor_temp", round(motor_t, 1), mode)
                yield (v.vehicle_id, ts, "ambient_temp", round(amb, 1), mode)

            # Projected range on a full charge at this trip's consumption,
            # which is what "range dropped" means and what the baseline
            # is rated in.
            end_ts = t + timedelta(minutes=minutes)
            projected = cap_wh * health / 100 / wh_per_km * rng.gauss(1, 0.03)
            yield (v.vehicle_id, end_ts, "range_estimate", round(projected, 1), mode)
            if soc < 25:
                soc = rng.uniform(85, 100)           # midday top-up

        # Nightly charge: power readings every 30 minutes for the first
        # two hours. A faulty charger delivers under half its rating,
        # so the same top-up takes more than twice as long.
        power = 0.34 if is_active(v, "charger_fault", day) else STANDARD_CHARGER_KW
        plug_in = datetime.combine(day, time(22, rng.randrange(0, 60, 5)), IST)
        for k in range(4):
            yield (v.vehicle_id, plug_in + timedelta(minutes=30 * k), "charge_power",
                   round(max(rng.gauss(power, 0.03), 0.05), 3), CHARGING)


# ── real-world flaws ─────────────────────────────────────────
#
# Clean data never tests anything. Real telemetry drops readings,
# goes silent when a vehicle loses connectivity, and occasionally
# reports nonsense — a current spike, a sensor reading zero, a
# temperature probe stuck at -40. An AVG over that is wrong in ways a
# query written against clean data never has to handle.
#
# metric_value is NOT NULL, so missing sensor data shows up the way it
# does in a real feed: as absent rows. Real NULLs are seeded where the
# schema allows them — vehicles that never reported an odometer or a
# registered region.
#
# Flaws draw from their OWN random stream, so the clean story (which
# vehicle has which fault, and its magnitude) is identical with or
# without them.

DROP_RATE = 0.01          # a single reading lost in transit
GLITCH_RATE = 0.0005      # a reading that is simply wrong
OFFLINE_SHARE = 0.15      # vehicles that go silent for 1-3 days
NO_ODOMETER_SHARE = 0.03
NO_REGION_SHARE = 0.02
FLAWED_METRICS = {"speed", "current_draw", "pack_voltage", "motor_temp"}


def add_real_world_flaws(vehicles: list[Vehicle], start: date, end: date,
                         rng: random.Random) -> None:
    span = (end - start).days
    for v in vehicles:
        # Planted-scenario vehicles keep continuous data: a demo whose
        # evidence happens to fall in an outage demonstrates nothing.
        if v.scenario is None and rng.random() < OFFLINE_SHARE and span > 3:
            for _ in range(rng.randint(1, 3)):
                first = start + timedelta(days=rng.randrange(span))
                v.offline_days.update(first + timedelta(days=k) for k in range(rng.randint(1, 3)))
        v.reports_odometer = rng.random() >= NO_ODOMETER_SHARE
        v.reports_region = rng.random() >= NO_REGION_SHARE


def glitch(metric: str, value: float, rng: random.Random) -> float:
    if metric == "motor_temp":
        return -40.0                                  # probe fault sentinel
    if metric == "current_draw" and rng.random() < 0.5:
        return round(value * rng.uniform(3, 6), 2)    # spike
    return 0.0                                        # dropout reads zero


def with_flaws(v: Vehicle, rows: Iterable[tuple], rng: random.Random,
               tally: dict[str, int]) -> Iterable[tuple]:
    for vid, ts, metric, value, mode in rows:
        if ts.date() in v.offline_days:
            tally["offline"] += 1
            continue
        if metric in FLAWED_METRICS:
            if rng.random() < DROP_RATE:
                tally["dropped"] += 1
                continue
            if rng.random() < GLITCH_RATE:
                value = glitch(metric, value, rng)
                tally["glitched"] += 1
        yield vid, ts, metric, value, mode


# ── service events ───────────────────────────────────────────


def service_events_for(v: Vehicle, ref: dict[str, Any], end: date, rng: random.Random) -> list[tuple]:
    rows: list[tuple] = []
    history_start = max(v.sold_on, end - timedelta(days=365))

    d = history_start + timedelta(days=rng.randint(60, 120))
    while d < end:
        rows.append((v.vehicle_id, d, "scheduled", "Periodic service", None, "Routine checks completed"))
        d += timedelta(days=rng.randint(110, 130))

    issues = {i["issue"]: i["share"] for i in ref["reported_issues"]}
    days_span = max((end - history_start).days, 1)
    for _ in range(rng.choices([0, 1, 2, 3], weights=[40, 35, 18, 7])[0]):
        issue = weighted(rng, issues)
        when = history_start + timedelta(days=rng.randrange(days_span))
        code = rng.choice(ISSUE_CODES[issue])
        rows.append((v.vehicle_id, when, "complaint", issue, code, ISSUE_RESOLUTIONS[issue]))

    if v.scenario == "overload":
        rows.append((v.vehicle_id, end - timedelta(days=rng.randint(1, 3)), "complaint",
                     "Range dropped noticeably over the last week", "ERR_601", None))
    elif v.scenario == "cell_wear":
        rows.append((v.vehicle_id, end - timedelta(days=18), "complaint",
                     "Range getting shorter every week", "ERR_402", "Balance charge performed"))
        rows.append((v.vehicle_id, end - timedelta(days=2), "inspection",
                     "Cell health warning on dashboard", "ERR_403", None))
    elif v.scenario == "undocumented":
        rows.append((v.vehicle_id, end - timedelta(days=2), "complaint",
                     "Battery drains faster than usual", None, None))
    elif v.scenario == "speed_cap" and v.onset:
        rows.append((v.vehicle_id, v.onset + timedelta(days=rng.randint(1, 3)), "complaint",
                     "Scooter will not go above 45 km/h in Sonic mode", "ERR_205", None))
    elif v.scenario == "charger_fault" and v.onset:
        rows.append((v.vehicle_id, v.onset + timedelta(days=4), "complaint",
                     "Charging takes much longer than it used to", "ERR_303", None))
    return rows


# ── sales ────────────────────────────────────────────────────


def ultra_on_sale(ref: dict[str, Any], day: date, zone: str) -> bool:
    ultra = ref["models"]["Volt 1 Ultra"]
    first = date.fromisoformat(ultra["sold_from"] if zone == "south" else ultra["launched_elsewhere"])
    return day >= first


def model_mix(ref: dict[str, Any], day: date, zone: str) -> dict[str, float]:
    if day < date.fromisoformat(ref["models"]["Volt 1 Gen 2"]["sold_from"]):
        return {"Volt 1": 1.0}
    if not ultra_on_sale(ref, day, zone):
        return {"Volt 1": 0.45, "Volt 1 Gen 2": 0.55}
    return {"Volt 1": 0.28, "Volt 1 Gen 2": 0.42, "Volt 1 Ultra": 0.30}


def zone_mix(ref: dict[str, Any], day: date) -> dict[str, float]:
    # Newer zones grow slowly over the two years.
    months = (day.year - 2024) * 12 + day.month - 10
    shares = {z: r["share"] for z, r in ref["regions"].items()}
    growth = 1 + months * 0.02
    for z in ("west", "north", "east"):
        shares[z] *= growth
    # The Ultra launched in the south first: demand there jumps until
    # the other regions get it.
    if ultra_on_sale(ref, day, "south") and not ultra_on_sale(ref, day, "west"):
        shares["south"] *= 1.45
    return shares


CHANNELS = {"showroom": 0.62, "online": 0.18, "fleet": 0.12, "partner": 0.08}
DISCOUNT_CAP = {"showroom": 3.0, "online": 3.0, "partner": 7.0, "fleet": 10.0}
FINANCE_PARTNERS = ["FinCo A", "FinCo B", None, None]


def sale_row(
    ref: dict[str, Any], day: date, rng: random.Random,
    vehicle: Optional[Vehicle] = None,
) -> tuple:
    models = ref["models"]
    zone = vehicle.zone if vehicle else weighted(rng, zone_mix(ref, day))
    model = vehicle.model_code if vehicle else weighted(rng, model_mix(ref, day, zone))
    city = vehicle.city if vehicle else rng.choice(ref["regions"][zone]["cities"])

    channels = dict(CHANNELS)
    if zone == "south":
        channels["fleet"] *= 1.6
    channel = "fleet" if (vehicle and vehicle.usage == "fleet") else weighted(rng, channels)

    discount = round(rng.uniform(0, DISCOUNT_CAP[channel]), 1)
    price = round(models[model]["price_inr"] * (1 - discount / 100), 2)
    meta: dict[str, Any] = {"discount_pct": discount}
    partner = rng.choice(FINANCE_PARTNERS)
    if partner and channel in ("showroom", "online"):
        meta["finance_partner"] = partner
    if channel == "fleet":
        meta["fleet_size"] = rng.choice([25, 40, 60, 80, 120])
    dealer = f"D-{city[:3].upper()}-{rng.randint(1, 6):02d}"
    return (
        vehicle.vehicle_id if vehicle else None, day, zone, channel, price, dealer,
        json.dumps(meta), model, city,
    )


def sales_rows(ref: dict[str, Any], vehicles: list[Vehicle], end: date, scale: float, rng: random.Random) -> list[tuple]:
    rows = [sale_row(ref, v.sold_on, rng, v) for v in vehicles]
    linked = {}
    for v in vehicles:
        key = v.sold_on.strftime("%Y-%m")
        linked[key] = linked.get(key, 0) + 1

    for month, units in ref["monthly_sales"]["curve"].items():
        y, m = map(int, month.split("-"))
        first = date(y, m, 1)
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        last = min(last, end)
        if first > end:
            continue
        target = max(round(units * scale) - linked.get(month, 0), 0)
        span = (last - first).days + 1
        for _ in range(target):
            # Weekends sell ~30% more.
            while True:
                day = first + timedelta(days=rng.randrange(span))
                if day.weekday() >= 5 or rng.random() < 0.77:
                    break
            rows.append(sale_row(ref, day, rng))
    return rows


# ── output sinks ─────────────────────────────────────────────


def database_url(arg: Optional[str]) -> str:
    url = arg or os.environ.get("DATABASE_URL", "")
    if not url or "${" in url:
        url = "postgresql://{u}:{p}@{h}:{port}/{db}".format(
            u=os.environ.get("POSTGRES_USER", "opscopilot"),
            p=os.environ.get("POSTGRES_PASSWORD", "change_me_locally"),
            h=os.environ.get("POSTGRES_HOST", "localhost"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
            db=os.environ.get("POSTGRES_DB", "opscopilot"),
        )
    return url.replace("postgresql+psycopg://", "postgresql://")


class Sink:
    """Writes to Postgres (COPY) and/or CSV files."""

    def __init__(self, conn: Any, csv_dir: Optional[Path]):
        self.conn = conn
        self.csv_dir = csv_dir
        self._csv: dict[str, tuple[Any, Any]] = {}
        if csv_dir:
            csv_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self, table: str, columns: list[str], rows: Iterable[tuple],
        db: bool = True, csv_out: bool = True,
    ) -> int:
        rows = list(rows)
        if not rows:
            return 0
        if db and self.conn is not None:
            with self.conn.cursor() as cur:
                with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
                    for r in rows:
                        cp.write_row(r)
        if csv_out and self.csv_dir:
            if table not in self._csv:
                fh = open(self.csv_dir / f"{table}.csv", "w", newline="")
                w = csv.writer(fh)
                w.writerow(columns)
                self._csv[table] = (fh, w)
            self._csv[table][1].writerows(rows)
        return len(rows)

    def close(self) -> None:
        for fh, _ in self._csv.values():
            fh.close()


LANE_A_TABLES = [
    "vehicle_telemetry", "service_events", "sales_transactions",
    "vehicles", "vehicle_baseline_specs", "error_codes",
]


def prepare_db(conn: Any, reset: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE sales_transactions ADD COLUMN IF NOT EXISTS model_code TEXT")
        cur.execute("ALTER TABLE sales_transactions ADD COLUMN IF NOT EXISTS city TEXT")
        if reset:
            cur.execute(f"TRUNCATE {', '.join(LANE_A_TABLES)} RESTART IDENTITY CASCADE")
        else:
            cur.execute("SELECT count(*) FROM vehicles")
            if cur.fetchone()[0]:
                sys.exit("vehicles already has rows; re-run with --reset to replace them")


# ── manifest ─────────────────────────────────────────────────


def measure(conn: Any, vid: str, since: date) -> dict[str, Any]:
    sql = """
        SELECT t.metric_name, avg(t.metric_value), avg(b.nominal_value)
        FROM vehicle_telemetry t
        JOIN vehicles v ON v.vehicle_id = t.vehicle_id
        LEFT JOIN vehicle_baseline_specs b
          ON b.model_code = v.model_code AND b.drive_mode = t.drive_mode
         AND b.metric_name = t.metric_name
        WHERE t.vehicle_id = %s AND t.recorded_at >= %s
          AND t.metric_name IN ('current_draw', 'payload', 'range_estimate', 'motor_temp',
                                'speed', 'charge_power')
        GROUP BY t.metric_name
    """
    out: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (vid, since))
        for metric, actual, base in cur.fetchall():
            out[metric] = {"actual": round(actual, 1), "baseline": round(base, 1) if base else None}
            if base:
                out[metric]["delta_pct"] = round((actual - base) / base * 100, 1)
        cur.execute(
            "SELECT metric_value FROM vehicle_telemetry WHERE vehicle_id=%s "
            "AND metric_name='cell_health' ORDER BY recorded_at DESC LIMIT 1", (vid,)
        )
        row = cur.fetchone()
        if row:
            out["cell_health_latest"] = row[0]
    return out


EXPECTED = {
    "overload": {
        "finding": "Current draw well above baseline; payload above rated; cell health normal",
        "cause": "Sustained overload",
        "documents": ["SB-114_sustained_overload"],
        "error_codes": ["ERR_601"],
    },
    "cell_wear": {
        "finding": "Cell health falling ~0.6% per week; current draw near baseline",
        "cause": "Removable pack capacity loss / cell imbalance",
        "documents": ["SB-121_removable_pack_cell_wear"],
        "error_codes": ["ERR_402", "ERR_403"],
    },
    "speed_cap": {
        "finding": "Speed in Sonic / Sonic X held near 42 km/h (baseline 62 / 70) since the 3.2.0 update",
        "cause": "Firmware 3.2.0 applies the Eco X speed limit to Sonic and Sonic X",
        "documents": ["SB-135_firmware_sonic_speed_cap"],
        "error_codes": ["ERR_205"],
    },
    "charger_fault": {
        "finding": "Charging power ~0.34 kW against a 0.75 kW baseline; riding metrics normal",
        "cause": "Fast charger delivering under half its rated power",
        "documents": ["SB-140_charger_low_power"],
        "error_codes": ["ERR_303"],
    },
    "undocumented": {
        "finding": "Current draw ~30% above baseline; payload and cell health normal",
        "cause": None,
        "documents": [],
        "note": "No document explains high current with normal payload and cell health; "
                "the correct answer is partial.",
    },
}


def write_manifest(conn: Any, vehicles: list[Vehicle], args: argparse.Namespace, start: date, end: date, counts: dict[str, int]) -> None:
    scenarios = []
    for v in vehicles:
        if not v.scenario:
            continue
        entry: dict[str, Any] = {
            "scenario": v.scenario,
            "vehicle_id": v.vehicle_id,
            "model_code": v.model_code,
            "onset": v.onset.isoformat() if v.onset else None,
            **EXPECTED[v.scenario],
        }
        if conn is not None:
            entry["measured_since_onset"] = measure(conn, v.vehicle_id, v.onset or start)
        scenarios.append(entry)

    MANIFEST.write_text(json.dumps({
        "generated_with": {
            "vehicles": args.vehicles, "days": args.days, "seed": args.seed,
            "interval_minutes": args.interval_minutes, "sales_scale": args.sales_scale,
        },
        "telemetry_window": {"start": start.isoformat(), "end": end.isoformat()},
        "row_counts": counts,
        "scenarios": scenarios,
    }, indent=2, default=str) + "\n")


# ── main ─────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--vehicles", type=int, default=120)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--end-date", type=date.fromisoformat, default=date.today(),
                   help="last day of telemetry (default: today)")
    p.add_argument("--interval-minutes", type=int, default=5,
                   help="reading interval while a vehicle is moving")
    p.add_argument("--sales-scale", type=float, default=1.0,
                   help="multiply the monthly sales curve (0.1 = 10%% of volume)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--database-url")
    p.add_argument("--reset", action="store_true", help="truncate seeded tables first")
    p.add_argument("--csv-dir", type=Path, help="also write every table as CSV here")
    p.add_argument("--no-db", action="store_true", help="CSV only; do not connect")
    p.add_argument("--clean", action="store_true",
                   help="no glitches, gaps or missing values (default: realistic flaws)")
    args = p.parse_args()

    if args.no_db and not args.csv_dir:
        sys.exit("--no-db needs --csv-dir")

    rng = random.Random(args.seed)
    ref = load_reference()
    end = args.end_date
    start = end - timedelta(days=args.days - 1)

    conn = None
    if not args.no_db:
        import psycopg
        conn = psycopg.connect(database_url(args.database_url))
        prepare_db(conn, args.reset)

    sink = Sink(conn, args.csv_dir)
    counts: dict[str, int] = {}

    error_rows = parse_error_codes(ERROR_CODE_DOC)
    counts["error_codes"] = sink.write(
        "error_codes",
        ["code", "subsystem", "meaning", "recommended_action", "severity", "source_document"],
        [tuple(r.values()) for r in error_rows],
    )
    counts["vehicle_baseline_specs"] = sink.write(
        "vehicle_baseline_specs",
        ["model_code", "drive_mode", "metric_name", "nominal_value", "tolerance_pct", "rated_payload_kg"],
        build_baselines(ref),
    )

    vehicles = build_vehicles(ref, args.vehicles, start, end, rng)
    flaw_rng = random.Random(args.seed + 1)
    if not args.clean:
        add_real_world_flaws(vehicles, start, end, flaw_rng)
    flaws = {"offline": 0, "dropped": 0, "glitched": 0}

    vehicle_cols = ["vehicle_id", "model_code", "manufactured_on", "registered_region", "odometer_km", "config"]

    def odometer(x: Vehicle) -> Optional[float]:
        return round(x.odometer_km, 1) if x.reports_odometer else None

    def vehicle_rows() -> list[tuple]:
        return [(x.vehicle_id, x.model_code, x.manufactured_on,
                 x.city if x.reports_region else None,
                 odometer(x), json.dumps(x.config)) for x in vehicles]

    # Rows must exist before telemetry references them; odometer is
    # final only after telemetry, so it is updated (DB) or written
    # (CSV) afterwards.
    counts["vehicles"] = sink.write("vehicles", vehicle_cols, vehicle_rows(), csv_out=False)

    telemetry_rows = 0
    tele_cols = ["vehicle_id", "recorded_at", "metric_name", "metric_value", "unit", "drive_mode"]
    for v in vehicles:
        readings = telemetry_for(v, ref, start, args.days, args.interval_minutes, rng)
        if not args.clean:
            readings = with_flaws(v, readings, flaw_rng, flaws)
        batch = [
            (vid, ts, metric, value, UNITS[metric], mode)
            for vid, ts, metric, value, mode in readings
        ]
        telemetry_rows += sink.write("vehicle_telemetry", tele_cols, batch)
        print(f"  {v.vehicle_id} {v.model_code:<13} {v.scenario or '':<14} {len(batch):>7} readings", flush=True)
    counts["vehicle_telemetry"] = telemetry_rows

    if conn is not None:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE vehicles SET odometer_km = %s WHERE vehicle_id = %s",
                [(odometer(v), v.vehicle_id) for v in vehicles],
            )
    if not args.clean:
        print(f"  real-world flaws: {flaws['glitched']} glitched readings, "
              f"{flaws['dropped']} dropped, {flaws['offline']} lost to offline days "
              f"({sum(bool(v.offline_days) for v in vehicles)} vehicles), "
              f"{sum(not v.reports_odometer for v in vehicles)} vehicles with no odometer, "
              f"{sum(not v.reports_region for v in vehicles)} with no region", flush=True)
    counts["vehicles"] = sink.write("vehicles", vehicle_cols, vehicle_rows(), db=False) or counts["vehicles"]

    counts["service_events"] = sink.write(
        "service_events",
        ["vehicle_id", "occurred_on", "event_type", "reported_issue", "error_code", "resolution"],
        [r for v in vehicles for r in service_events_for(v, ref, end, rng)],
    )
    counts["sales_transactions"] = sink.write(
        "sales_transactions",
        ["vehicle_id", "sold_on", "region", "channel", "unit_price", "dealer_code",
         "deal_metadata", "model_code", "city"],
        sales_rows(ref, vehicles, end, args.sales_scale, rng),
    )

    if conn is not None:
        conn.commit()
    write_manifest(conn, vehicles, args, start, end, counts)
    sink.close()
    if conn is not None:
        conn.close()

    print("\nSeeded:")
    for table, n in counts.items():
        print(f"  {table:<24} {n:>9,}")
    print(f"\nTelemetry window {start} to {end}. Scenarios in {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
