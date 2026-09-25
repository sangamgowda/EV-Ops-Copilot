"""Build the golden set: generated cases + hand-written ones, and traps.

    python scripts/build_golden.py                 # rebuild everything
    python scripts/build_golden.py --no-generate   # keep the generated
                                                   # document questions,
                                                   # refresh the rest
    python scripts/build_golden.py --check-traps   # only re-check traps

Writes, under src/ops_copilot/evaluation/golden/:
  golden.jsonl       every case the runner scores
  trap_cases.jsonl   abstention cases (reported separately)
  golden_meta.json   what was built from what, and what was rejected

Three sources, mixed:

  synthetic  from the seeded data: questions about the planted
             stories, lookups, error codes and sales, with the correct
             numbers computed here by SQL — ground truth known exactly.
             Plus questions written by the cheap model from WHOLE
             documents (never a single chunk), rejected when they share
             too many words with any chunk of their document, and never
             from the held-out documents.
  adversarial / promoted
             curated.yaml, written by hand. Numbers there come from
             `fact_sql`, filled in now.
  traps      traps.yaml. Before writing them, every known answer is
             searched for in every document: a trap whose answer IS in
             the data is not a trap, and building fails.

Needs the database the data was seeded into. Run it after seeding:
"this week" in a question means the week before the data ends.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel, Field  # noqa: E402

from ops_copilot.rag.ingest import split_front_matter  # noqa: E402
from ops_copilot.settings import get_config, load_prompt  # noqa: E402

GOLDEN = ROOT / "src" / "ops_copilot" / "evaluation" / "golden"
DOCS = ROOT / "data" / "documents"
MANIFEST = ROOT / "data" / "seed_manifest.json"

WEEK = "t.recorded_at >= now() - interval '7 days'"
Q3 = "sold_on BETWEEN '2026-07-01' AND '2026-09-30'"
STOPWORDS = set("""a an and are as at be but by can do does for from how i if in is it its
of on or so that the their them then there these this to was what when where which who why
will with would you your my me we our any all has have had not no than too very just""".split())


def database_url(arg: str | None) -> str:
    url = arg or os.environ.get("DATABASE_URL", "")
    if not url or "${" in url:
        sys.exit("set --database-url or DATABASE_URL to the seeded database")
    return url.replace("postgresql+psycopg://", "postgresql://")


def q1(conn: Any, sql: str, *args: Any) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
    return None if row is None else row[0]


def r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(float(x), nd)


# ── synthetic: from seeded data ──────────────────────────────

def vs_baseline(conn: Any, vid: str, metric: str, modes: tuple[str, ...] | None = None) -> tuple[float, float]:
    """Average reading and average baseline over the last 7 days, matched
    mode by mode — the comparison the agent is expected to make."""
    mode_sql = "AND t.drive_mode = ANY(%s)" if modes else ""
    sql = f"""SELECT avg(t.metric_value), avg(b.nominal_value)
              FROM vehicle_telemetry t JOIN vehicles v ON v.vehicle_id = t.vehicle_id
              JOIN vehicle_baseline_specs b ON b.model_code = v.model_code
               AND b.drive_mode = t.drive_mode AND b.metric_name = t.metric_name
              WHERE t.vehicle_id = %s AND t.metric_name = %s AND {WEEK} {mode_sql}"""
    with conn.cursor() as cur:
        cur.execute(sql, (vid, metric, list(modes)) if modes else (vid, metric))
        actual, base = cur.fetchone()
    return float(actual), float(base)


def diag(case_id: str, vid: str, question: str, reference: str, facts: list[dict[str, Any]],
         must_contain: list[Any], key_args: list[str], must_not: list[str] | None = None,
         tags: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": case_id, "source": "synthetic_data", "tags": ["diagnostic", "explain", *(tags or [])],
        "question": question,
        "expected": {
            "domains": ["diagnostic"], "vehicle_id": vid,
            "tools": ["structured_query_tool", "rag_retrieval_tool"],
            "key_args": [{"tool": "structured_query_tool", "contains": [vid, *key_args]}],
            "facts": facts, "must_contain": must_contain,
            "must_not_contain": must_not or [],
            "max_iterations": 3, "stop_reasons": ["complete", "no_new_evidence"],
        },
        "reference_answer": reference,
    }


def story_cases(conn: Any) -> list[dict[str, Any]]:
    out = []
    for vid in ("V-042", "V-007"):
        cur, cur_b = vs_baseline(conn, vid, "current_draw")
        pay, _ = vs_baseline(conn, vid, "payload")
        health = r(q1(conn, "SELECT metric_value FROM vehicle_telemetry WHERE vehicle_id = %s "
                            "AND metric_name = 'cell_health' ORDER BY recorded_at DESC LIMIT 1", vid))
        delta = round((cur - cur_b) / cur_b * 100, 1)
        out.append(diag(
            f"syn_overload_{vid}", vid, f"Why did range drop on {vid} this week?",
            f"{vid} is overloaded: payload averages about {pay:.0f} kg against a 150 kg rating, "
            f"so current draw is about {delta:.0f}% above baseline ({cur:.1f} A vs {cur_b:.1f} A). "
            f"Battery health is normal ({health}%), so wear is ruled out. SB-114: reduce the load.",
            [{"label": "current draw above baseline pct", "value": delta, "tolerance": 6},
             # ±5, not wider: the answer also carries range numbers (~184 km)
             # that a looser tolerance would accept as the payload.
             {"label": "payload kg", "value": r(pay, 0), "tolerance": 5}],
            [["overload", "overloaded", "above the rated", "exceeds the rated", "over the rated"]],
            ["current_draw"], ["battery degradation is the cause", "caused by battery wear"],
            ["story:overload"]))
    for vid in ("V-055", "V-088"):
        spd, spd_b = vs_baseline(conn, vid, "speed", ("Sonic", "Sonic X"))
        out.append(diag(
            f"syn_speed_cap_{vid}", vid, f"Why won't {vid} go above 45 km/h in Sonic mode?",
            f"{vid} is on firmware 3.2.0, which applies the Eco X 45 km/h limit to Sonic and Sonic X "
            f"(SB-135). Its speed in those modes averages about {spd:.0f} km/h against a baseline of "
            f"{spd_b:.0f}. Updating to 3.2.1 restores the limits.",
            [{"label": "sonic speed kmh", "value": r(spd, 0), "tolerance": 4}],
            [["firmware", "3.2.0"]], ["speed"], tags=["story:speed_cap"]))
    pw, pw_b = vs_baseline(conn, "V-091", "charge_power")
    out.append(diag(
        "syn_charger_V-091", "V-091", "Why is V-091 taking so long to charge?",
        f"V-091's charger delivers about {pw:.2f} kW against a {pw_b:.2f} kW baseline, under half "
        "its rating, so charging takes more than twice as long; riding and battery health are "
        "normal. SB-140: test and replace the charger.",
        [{"label": "charge power kw", "value": r(pw, 2), "tolerance": 0.06}],
        ["charger"], ["charge_power"], tags=["story:charger_fault"]))
    health = r(q1(conn, "SELECT metric_value FROM vehicle_telemetry WHERE vehicle_id = 'V-036' "
                        "AND metric_name = 'cell_health' ORDER BY recorded_at DESC LIMIT 1"))
    out.append(diag(
        "syn_cell_wear_V-036", "V-036", "Why is the range on V-036 getting shorter every week?",
        f"Battery wear: V-036's cell health has fallen steadily to about {health}% while current "
        "draw and payload are normal (SB-121). Balance-charge the removable pack, then capacity-test "
        "it if still below 85%.",
        [{"label": "cell health pct", "value": health, "tolerance": 1.5}],
        [["battery", "cell health", "degradation", "wear"]], ["cell_health"],
        ["caused by overload", "due to overload"], tags=["story:cell_wear"]))
    return out


def lookup(case_id: str, question: str, reference: str, domain: str, *, facts: list[dict[str, Any]] | None = None,
           must_contain: list[Any] | None = None, key: list[str] | None = None,
           vid: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    exp: dict[str, Any] = {
        "domains": [domain], "tools": ["structured_query_tool"],
        "max_iterations": 2, "stop_reasons": ["complete", "no_new_evidence"],
    }
    if vid:
        exp["vehicle_id"] = vid
    if key:
        exp["key_args"] = [{"tool": "structured_query_tool", "contains": key}]
    if facts:
        exp["facts"] = facts
    if must_contain:
        exp["must_contain"] = must_contain
    return {"id": case_id, "source": "synthetic_data", "tags": [domain, "lookup", *(tags or [])],
            "question": question, "expected": exp, "reference_answer": reference}


def lookup_cases(conn: Any) -> list[dict[str, Any]]:
    out = []
    normal_ultra = q1(conn, "SELECT vehicle_id FROM vehicles WHERE model_code = 'Volt 1 Ultra' "
                            "AND config->>'firmware_version' <> '3.2.0' ORDER BY vehicle_id LIMIT 1")
    for vid in ("V-055", normal_ultra):
        fw = q1(conn, "SELECT config->>'firmware_version' FROM vehicles WHERE vehicle_id = %s", vid)
        out.append(lookup(f"syn_firmware_{vid}", f"What firmware version is {vid} running?",
                          f"{vid} is on firmware {fw}.", "diagnostic", must_contain=[fw], key=[vid], vid=vid))
    health = r(q1(conn, "SELECT metric_value FROM vehicle_telemetry WHERE vehicle_id = 'V-042' "
                        "AND metric_name = 'cell_health' ORDER BY recorded_at DESC LIMIT 1"))
    out.append(lookup("syn_cell_health_V-042", "What is the latest cell health reading for V-042?",
                      f"V-042's latest cell health reading is {health}%.", "diagnostic",
                      facts=[{"label": "cell health pct", "value": health, "tolerance": 0.5}],
                      key=["V-042", "cell_health"], vid="V-042"))
    vid, odo = conn.execute("SELECT vehicle_id, odometer_km FROM vehicles WHERE odometer_km IS NOT NULL "
                            "AND vehicle_id >= 'V-100' ORDER BY vehicle_id LIMIT 1").fetchone()
    out.append(lookup(f"syn_odometer_{vid}", f"What is the odometer reading on {vid}?",
                      f"{vid} has covered about {odo:,.0f} km.", "diagnostic",
                      facts=[{"label": "odometer km", "value": r(odo, 0), "tolerance": 1}], key=[vid], vid=vid))
    city = q1(conn, "SELECT registered_region FROM vehicles WHERE vehicle_id = 'V-064'")
    out.append(lookup("syn_city_V-064", "Which city is V-064 registered in?", f"V-064 is registered in {city}.",
                      "diagnostic", must_contain=[city], key=["V-064"], vid="V-064"))

    codes = {"ERR_205": [["speed limit", "speed-limit"]], "ERR_303": [["charger"], ["below", "under"]],
             "ERR_403": [["85"]]}
    for code, must in codes.items():
        meaning, action = conn.execute("SELECT meaning, recommended_action FROM error_codes WHERE code = %s",
                                       (code,)).fetchone()
        out.append(lookup(f"syn_code_{code}", f"What does error code {code} mean?",
                          f"{code}: {meaning}. Recommended action: {action}.", "diagnostic",
                          must_contain=must, key=[code], tags=["error_code"]))

    ultra_south = q1(conn, f"SELECT count(*) FROM sales_transactions WHERE model_code = 'Volt 1 Ultra' "
                           f"AND region = 'south' AND {Q3}")
    out.append(lookup("syn_sales_ultra_south_q3",
                      "How many Volt 1 Ultras were sold in the south between 1 July and 30 September 2026?",
                      f"{ultra_south} Volt 1 Ultras were sold in the south in that period.", "business",
                      facts=[{"label": "ultra south q3 units", "value": ultra_south, "tolerance": 0}],
                      key=["Volt 1 Ultra", "south"]))
    with conn.cursor() as cur:
        cur.execute(f"SELECT region, count(*) FROM sales_transactions WHERE {Q3} GROUP BY 1 ORDER BY 2 DESC")
        top, top_n = cur.fetchone()
    out.append(lookup("syn_sales_top_region_q3",
                      "Which region sold the most scooters between 1 July and 30 September 2026?",
                      f"The {top}, with {top_n} sales.", "business", must_contain=[top],
                      facts=[{"label": "top region units", "value": top_n, "tolerance": 0}]))
    price = q1(conn, "SELECT avg(unit_price) FROM sales_transactions WHERE model_code = 'Volt 1 Gen 2' "
                     "AND sold_on >= '2026-01-01'")
    out.append(lookup("syn_sales_gen2_avg_price_2026",
                      "What was the average selling price of a Volt 1 Gen 2 in 2026?",
                      f"About ₹{price:,.0f} (list price ₹1,45,000, less discounts).", "business",
                      facts=[{"label": "avg price inr", "value": r(price, 0), "tolerance": 600}],
                      key=["Volt 1 Gen 2"]))
    fleet_aug = q1(conn, "SELECT count(*) FROM sales_transactions WHERE channel = 'fleet' "
                         "AND sold_on BETWEEN '2026-08-01' AND '2026-08-31'")
    out.append(lookup("syn_sales_fleet_aug", "How many scooters were sold through the fleet channel in August 2026?",
                      f"{fleet_aug} scooters.", "business",
                      facts=[{"label": "fleet aug units", "value": fleet_aug, "tolerance": 0}], key=["fleet"]))
    q2s = q1(conn, "SELECT count(*) FROM sales_transactions WHERE region = 'south' "
                   "AND sold_on BETWEEN '2026-04-01' AND '2026-06-30'")
    q3s = q1(conn, f"SELECT count(*) FROM sales_transactions WHERE region = 'south' AND {Q3}")
    out.append(lookup("syn_sales_south_q2_vs_q3",
                      "How did scooter sales in the south in April to June 2026 compare with July to September 2026?",
                      f"South sales rose from {q2s} in April to June to {q3s} in July to September 2026.", "business",
                      facts=[{"label": "south q2", "value": q2s, "tolerance": 0},
                             {"label": "south q3", "value": q3s, "tolerance": 0}], key=["south"]))
    return out


# ── synthetic: from whole documents ──────────────────────────

class GeneratedQuestion(BaseModel):
    question: str
    reference_answer: str
    must_contain: list[str] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def sanitize(g: GeneratedQuestion) -> tuple[list[str], list[dict[str, Any]]]:
    """Turn the generator's expectations into ones a correct answer can
    actually meet. Seen in practice:
      - numbers inside must_contain phrases ("750 W", "2 h 7 min",
        "15 August") fail on any rewording, so they become facts with
        a tolerance, and only wordless phrases stay as phrases;
      - placeholder facts of 0 for things that are not numbers (a date,
        a range);
      - facts copied from the question ("the charger shows 350 W"): a
        good answer need not repeat them."""
    question_numbers = {float(n.replace(",", "")) for n in _NUM.findall(g.question)}
    phrases, values = [], []
    for item in g.must_contain:
        found = _NUM.findall(item)
        if found:
            values += [float(n.replace(",", "")) for n in found]
        elif item.strip() and len(item.split()) <= 2:
            # Longer phrases ("Ride and Air") fail on any rewording.
            phrases.append(item.strip())
    for f in g.facts:
        v = f.get("value")
        if isinstance(v, int | float) and not isinstance(v, bool):
            values.append(float(v))
    facts, seen = [], set()
    for v in values:
        if v == 0 or v in question_numbers or v in seen:
            continue
        seen.add(v)
        facts.append({"label": f"{v:g}", "value": int(v) if v.is_integer() else v, "tolerance": 0})
    return phrases[:3], facts


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def overlap(question: str, chunk: str) -> float:
    """Share of the question's content words that appear in the chunk."""
    q = content_words(question)
    return 0.0 if not q else len(q & content_words(chunk)) / len(q)


async def doc_cases(conn: Any, limit_overlap: float, holdout: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from ops_copilot.llm.client import complete_json

    system, version = load_prompt("eval_generate")
    cases, rejected = [], []
    for path in sorted(DOCS.glob("*.md")):
        doc_id = path.stem
        if doc_id in holdout or doc_id == "manual_error_codes":
            continue          # held out; error codes are tested by SQL lookups
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM document_chunks WHERE doc_id = %s", (doc_id,))
            chunks = [c for (c,) in cur.fetchall()]
        user = f"## Document\n{body}"
        for attempt in range(3):
            g = await complete_json("eval_generate", system, user, GeneratedQuestion)
            worst = max((overlap(g.question, c) for c in chunks), default=0.0)
            if worst <= limit_overlap:
                phrases, facts = sanitize(g)
                cases.append({
                    "id": f"doc_{doc_id}", "source": "synthetic_doc",
                    "tags": [meta.get("domain", "diagnostic"), "document", f"doc:{doc_id}"],
                    "question": g.question,
                    "expected": {
                        "domains": [meta.get("domain", "diagnostic")], "tools": ["rag_retrieval_tool"],
                        "must_contain": phrases,
                        "facts": facts,
                        "max_iterations": 3,
                    },
                    "reference_answer": g.reference_answer,
                    "generation": {"prompt_version": version, "overlap": round(worst, 2), "attempt": attempt + 1},
                })
                print(f"  doc  {doc_id:<44} overlap {worst:.2f}  {g.question[:70]}")
                break
            rejected.append({"doc_id": doc_id, "question": g.question, "overlap": round(worst, 2)})
            print(f"  REJ  {doc_id:<44} overlap {worst:.2f}  {g.question[:70]}")
            user = (f"## Document\n{body}\n\n## Your previous question reused the document's wording "
                    f"({worst:.0%} of its words appear in one passage). Paraphrase much more.")
    return cases, rejected


# ── curated and traps ────────────────────────────────────────

def curated_cases(conn: Any) -> list[dict[str, Any]]:
    cases = []
    for name in ("curated.yaml", "promoted.yaml"):   # hand-written, and promoted after review
        path = GOLDEN / name
        if path.exists():
            cases += yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for case in cases:
        values = {}
        for fact in case.get("expected", {}).get("facts", []):
            if "fact_sql" in fact:
                v = q1(conn, fact.pop("fact_sql"))
                fact["value"] = r(v, 2) if isinstance(v, float) else (float(v) if v is not None else None)
                if fact["value"] is not None and float(fact["value"]).is_integer():
                    fact["value"] = int(fact["value"])
            values[fact["label"]] = fact.get("value")
        case["reference_answer"] = " ".join(case["reference_answer"].split())
        for label, v in values.items():
            case["reference_answer"] = case["reference_answer"].replace("{" + label + "}", str(v))
        case["question"] = " ".join(case["question"].split())
    return cases


def trap_cases() -> list[dict[str, Any]]:
    traps = yaml.safe_load((GOLDEN / "traps.yaml").read_text(encoding="utf-8"))
    return [{"id": t["id"], "source": "trap", "tags": ["trap", "abstain"], "question": t["question"],
             "known_answer": t["known_answer"],
             "expected": {"abstain": True, "must_not_contain": t["known_answer"], "max_iterations": 3},
             "reference_answer": " ".join(t["reference_answer"].split())} for t in traps]


def check_traps(conn: Any, traps: list[dict[str, Any]]) -> list[str]:
    """A trap whose answer is in the data is not a trap. Whole words
    only: "psi" inside "collapsible" is not a tyre pressure."""
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id, content FROM document_chunks")
        chunks = cur.fetchall()
    leaks = []
    for t in traps:
        for term in t["known_answer"]:
            pat = re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.IGNORECASE)
            for doc_id, content in chunks:
                m = pat.search(content)
                if m:
                    ctx = " ".join(content[max(m.start() - 50, 0):m.end() + 50].split())
                    leaks.append(f"{t['id']}: '{term.strip()}' in {doc_id}: ...{ctx}...")
    return leaks


# ── main ─────────────────────────────────────────────────────

def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database-url")
    p.add_argument("--no-generate", action="store_true", help="reuse existing document questions")
    p.add_argument("--check-traps", action="store_true", help="only check trap answers are absent")
    args = p.parse_args()

    import psycopg

    cfg = get_config()["evaluation"]
    conn = psycopg.connect(database_url(args.database_url))
    traps = trap_cases()
    leaks = check_traps(conn, traps)
    if leaks:
        print("Trap answers found in the documents — these are not traps:\n  " + "\n  ".join(leaks))
        return 1
    print(f"{len(traps)} traps checked: no known answer appears in any document")
    if args.check_traps:
        return 0

    synthetic = story_cases(conn) + lookup_cases(conn)
    rejected: list[dict[str, Any]] = []
    if args.no_generate:
        old = [json.loads(line) for line in (GOLDEN / "golden.jsonl").read_text(encoding="utf-8").splitlines()]
        docs = [c for c in old if c["source"] == "synthetic_doc"]
        meta_path = GOLDEN / "golden_meta.json"
        if meta_path.exists():   # the questions are reused, so is the record of what was rejected
            rejected = json.loads(meta_path.read_text(encoding="utf-8")).get("rejected_document_questions", [])
    else:
        docs, rejected = await doc_cases(conn, cfg["max_question_chunk_overlap"], set(cfg["holdout_documents"]))
    curated = curated_cases(conn)
    cases = synthetic + docs + curated

    ids = [c["id"] for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        print(f"duplicate case ids: {sorted(dupes)}")
        return 1
    missing = [c["id"] for c in cases for f in c["expected"].get("facts", []) if f.get("value") is None]
    if missing:
        print(f"facts with no value (check fact_sql): {missing}")
        return 1

    write_jsonl(GOLDEN / "golden.jsonl", cases)
    write_jsonl(GOLDEN / "trap_cases.jsonl", traps)
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["source"]] = counts.get(c["source"], 0) + 1
    meta = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed_manifest_sha": hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:12],
        "data_ends": json.loads(MANIFEST.read_text(encoding="utf-8"))["telemetry_window"]["end"],
        "counts": counts, "traps": len(traps),
        "holdout_documents": cfg["holdout_documents"],
        "max_question_chunk_overlap": cfg["max_question_chunk_overlap"],
        "generator_prompt_version": load_prompt("eval_generate")[1],
        "rejected_document_questions": rejected,
    }
    (GOLDEN / "golden_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                                             encoding="utf-8")
    total = len(cases)
    print(f"\n{total} cases: " + ", ".join(f"{k} {v} ({v / total:.0%})" for k, v in counts.items())
          + f"; {len(traps)} traps; {len(rejected)} generated questions rejected for word overlap")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
