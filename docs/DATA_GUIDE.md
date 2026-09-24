# Data guide — what is in the database, and what to ask

Everything here is **made-up but realistic** data about one family of
electric scooters. It exists so you can ask questions and know in
advance what a correct answer looks like.

Numbers below come from the default load
(`python scripts/seed_synthetic_data.py --reset`: 120 vehicles, 90 days).
Readings always cover **the 90 days up to the day you load the data**,
so "this week" means the last 7 days before loading. Exact values
shift slightly if you re-seed with different settings; the stories do
not.

---

## 1. The scooters

Three models. Specs for the first two follow the maker's published
figures (brand names replaced); the Ultra is invented as a top model.

| | Volt 1 | Volt 1 Gen 2 | Volt 1 Ultra |
|---|---|---|---|
| Battery | 3.7 kWh | 5.0 kWh | 6.4 kWh |
| Certified range | 155 km | 212 km | 248 km |
| Top speed | 100 km/h | 100 km/h | 115 km/h |
| 0–40 km/h | 2.55 s | 2.77 s | 2.4 s |
| Peak power / torque | 8.5 kW / 72 Nm | 8.5 kW / 72 Nm | 10.5 kW / 80 Nm |
| Weight | 126 kg | 134 kg | 139 kg |
| Price | ₹1,24,999 | ₹1,45,000 | ₹1,74,999 |
| On sale from | Oct 2024 | Jun 2025 | Jun 2026 (south), 15 Aug 2026 (elsewhere) |

All three: fixed + removable battery, 30-litre boot, 7-inch
touchscreen, IP67 water resistance, disc brakes with combined braking
(CBS), 150 kg rated load, 750 W charger (₹13,000 accessory), warranty
3 years / 30,000 km.

### Ride modes

| Mode | Speed limit | Torque | Feel |
|---|---|---|---|
| Eco X | 45 km/h | 25 Nm | very low torque, longest range |
| Eco | 50 km/h | 35 Nm | low torque |
| Ride | 70 km/h | 50 Nm | everyday, good torque |
| Air | 90 km/h | 60 Nm | high speed |
| Sonic | 100 km/h | 72 Nm | full performance |
| Sonic X | 115 km/h | 80 Nm | **Ultra only** |

---

## 2. What is stored

| Table | Rows | What one row is |
|---|---|---|
| `vehicles` | 120 | one scooter: **V-001 to V-120** (40 Volt 1, 48 Gen 2, 32 Ultra), its city, firmware, features |
| `vehicle_telemetry` | ~1.5 million | one sensor reading: speed, current draw, battery voltage, charge, motor temperature, load, range estimate, cell health, charging power |
| `vehicle_baseline_specs` | 115 | the **normal** value per model and ride mode — what readings are compared against |
| `service_events` | ~360 | a workshop visit or customer complaint, often with an error code |
| `error_codes` | 16 | what each trouble code (ERR_101 … ERR_801) means and what to do |
| `sales_transactions` | ~20,000 | one scooter sold: date, region, city, model, channel, price |
| `documents` | 23 | 12 service bulletins, manuals and business reports, plus 11 blog articles |

Regions: **south** (Bengaluru, Chennai, Hyderabad, Kochi, Mysuru,
Coimbatore), **west** (Pune, Mumbai, Goa), **north** (Delhi, Jaipur),
**east** (Kolkata, Bhubaneswar).

The data is deliberately a little messy, like real data: about 1% of
readings are missing, a few are glitches (a current spike, a
temperature of −40), some vehicles go offline for a day or two, and a
handful never reported their odometer or city.

---

## 3. The stories — vehicles with a known problem

These are planted on purpose. Each has a clear cause in the data and a
document that explains it, except the last, where the right answer is
"I can't tell".

| Vehicles | What you'd notice | What the data shows | Explained by |
|---|---|---|---|
| **V-042**, V-007, V-029 | range dropped this week | current draw **+38%**, load **~190 kg** (limit 150), battery healthy, range estimate **−26%** | SB-114 overload |
| **V-012**, V-055, V-088 (all Ultra) | won't go past 45 km/h | speed in Sonic / Sonic X stuck near **42 km/h** (normal 62 / 70) since the firmware 3.2.0 update ~10 days ago; other modes normal | SB-135 firmware, code ERR_205 |
| **V-064**, V-091 (Gen 2) | charging takes forever | charging power **0.34 kW** vs normal 0.75 (**−55%**) for the last 12 days; riding normal | SB-140 charger, code ERR_303 |
| **V-017**, V-036 (Volt 1) | range shrinking week by week | cell health falling steadily **92% → 84%** over 90 days; current draw normal | SB-121 battery wear, codes ERR_402 / ERR_403 |
| **V-023** | using more power | current draw **+30%**, but load, battery and speed all normal | **nothing** — the honest answer is "high power use, cause not found" |

---

## 4. Questions to ask, and what a good answer says

### Vehicle problems (numbers + documents)

| Ask | A good answer |
|---|---|
| Why did range drop on V-042 this week? | Overloaded: ~190 kg against a 150 kg limit, current draw ~38% above normal, battery healthy. Cites SB-114. |
| Why won't V-012 go above 45 km/h in Sonic mode? | Firmware 3.2.0 applies the Eco X limit to Sonic and Sonic X; speed ~42 vs 62 normal. Fix: update to 3.2.1. Cites SB-135. |
| Why is V-064 taking so long to charge? | Charger delivering ~0.3 kW instead of ~0.75; battery fine. Replace the charger. Cites SB-140. |
| Why is the range on V-017 getting shorter every week? | Battery wear: cell health down to ~84%, current normal. Balance charge, then capacity test. Cites SB-121. |
| Why is V-023 using more power than normal? | Current ~30% high; overload and battery wear ruled out; **no document explains it**. Should NOT claim a cause. |
| Is V-042's battery healthy? | Yes — cell health around 97%. |
| What firmware is V-055 on? | 3.2.0 (the faulty one). |

### Specs and how-to (documents)

| Ask | A good answer |
|---|---|
| What ride modes does the Volt 1 Ultra have and how fast does each go? | Six: Eco X 45, Eco 50, Ride 70, Air 90, Sonic 100, Sonic X 115 km/h. |
| How long does the removable battery take to charge to 80%? | About 2 h 7 min (fixed pack: about 3 h 47 min). |
| How much does the Volt 1 Gen 2 cost? | ₹1,45,000 ex-showroom. |
| How big is the boot? | 30 litres. |
| Can I ride it in the rain? | Yes — battery and motor are IP67 rated. |
| What does combined braking (CBS) do? | Using the rear lever brakes both wheels together. |
| Is an electric scooter cheaper to run than petrol? | Yes — about ₹0.25 per km. |

### Error codes (exact lookup)

| Ask | A good answer |
|---|---|
| What does ERR_205 mean? | Ride-mode speed limit mismatch — update firmware 3.2.0 to 3.2.1. |
| What does ERR_403 mean? | Cell health below 85% — critical; book a pack capacity test. |

### Business (sales data + reports)

| Ask | A good answer |
|---|---|
| Why did the south outsell the other regions this quarter? | The Ultra launched in the south on 1 June, ten weeks before anywhere else. South Q3 ≈ 2,270 sales (up from ≈ 2,060 in Q2); other regions roughly flat. |
| How many Volt 1 Ultras were sold in the south this quarter? | About 660. |
| Which model sold the most this year? | Volt 1 Gen 2 (~6,000), ahead of Volt 1 (~4,700). |
| Which model has sold the most of all time? | Volt 1 (~10,000) — it was the only model for its first eight months. A good answer notices the period matters. |
| What's the maximum discount on a fleet deal? | 10%, for 25 units or more. |

### Trick questions (it should NOT make things up)

| Ask | A good answer |
|---|---|
| Why did range drop on V-999? | There is no vehicle V-999. |
| Why did range drop on V42? | Treats it as V-042 (same vehicle, different spelling). |
| What is the top speed of the Volt 2? | No such model in the data. |

---

## 5. Where it all comes from

| Piece | File |
|---|---|
| Models, modes, prices, sales curve, regions | `data/reference/ev_models.yaml` |
| The generator (and the stories) | `scripts/seed_synthetic_data.py` |
| Measured numbers for each story, after loading | `data/seed_manifest.json` |
| Service bulletins, manuals, business reports | `data/documents/` |
| Blog articles | **not in the repo** — see below |

**The 11 blog articles** are the maker's own public posts, with brand
names swapped for "Volt". They are someone else's writing, so they
are fetched onto your machine and never committed:

```bash
cd ui && node scripts/render-pages.mjs ../data/sources.yaml ../data/raw/pages && cd ..
python scripts/pages_to_documents.py data/raw/pages data/raw/docs \
    --sources data/sources.yaml --anonymize data/anonymize.local.yaml
python scripts/ingest_documents.py data/raw/docs
```

`data/sources.yaml` (the page list) and `data/anonymize.local.yaml`
(the name swaps) are local files too. Without the articles everything
still works; the spec and how-to questions just have fewer sources.

---

## 6. One practical limit

The free tier of the AI provider allows about **200,000 tokens a day**
on the larger model. One question uses roughly 8,000–15,000, so expect
**15–20 questions a day**. When the allowance runs out, answers come
back as "could not write a full answer" with the evidence found so far;
it resets over the following hours. Space questions about 30 seconds
apart to avoid the per-minute limit as well.
