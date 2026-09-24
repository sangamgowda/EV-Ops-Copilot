"""Call each MCP tool once, over the protocol, and show what comes back.

This is the agent's side of the wire: it uses the MCP client only, never
the tool code, and it holds no database credentials. Run it where the
agent runs (the api container), with the MCP server up:

  python scripts/call_tools.py

It also shows the turn-ID check: a result tagged for an older turn is
discarded rather than used.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.mcp_client.client import MCPClient  # noqa: E402
from ops_copilot.settings import get_settings  # noqa: E402

CALLS = [
    ("resolve_entity_tool", {"vehicle_id": "V-O42"}),
    ("structured_query_tool", {"sql": (
        "SELECT v.vehicle_id, v.model_code, b.nominal_value AS baseline_current_a "
        "FROM vehicles v JOIN vehicle_baseline_specs b ON b.model_code = v.model_code "
        "WHERE v.vehicle_id = 'V-042' AND b.metric_name = 'current_draw' "
        "AND b.drive_mode = 'Ride'")}),
    ("structured_query_tool", {"sql": "DELETE FROM vehicles"}),
    ("rag_retrieval_tool", {"query": "range dropped and current draw is high with heavy loads",
                            "domain": "diagnostic",
                            "entity_id": "V-042"}),
    ("rag_retrieval_tool", {"query": "how do I replace the windscreen wiper motor",
                            "domain": "diagnostic"}),
]


def summarise(result: dict) -> str:
    keep = {k: result[k] for k in ("status", "stage", "reasons", "error", "rows", "explain_cost",
                                   "latency_ms", "best_score", "value", "suggestions", "note")
            if k in result and result[k] not in (None, [], "")}
    if result.get("chunks"):
        keep["chunks"] = [f"{c['rerank_score']:.2f} {c['doc_id']} > {c['section_path']}"
                          for c in result["chunks"]]
    return json.dumps(keep, indent=2, default=str)


async def main() -> None:
    s = get_settings()
    print(f"agent side: transport={s.mcp_transport}, "
          f"read-only DB credentials here: {'YES' if s.database_url_readonly else 'none'}\n")
    current_turn = "turn-2"
    async with MCPClient() as client:
        for name, args in CALLS:
            tagged = await client.call_tool(name, args, current_turn)
            print(f"── {name} {json.dumps(args)[:110]}")
            print(summarise(tagged["result"]), "\n")

        # A slow answer from an earlier turn arriving now must not be used.
        late = await client.call_tool("resolve_entity_tool", {"vehicle_id": "V-007"}, "turn-1")
        used = late["turn_id"] == current_turn
        print(f"── late result tagged {late['turn_id']!r} while on {current_turn!r}: "
              f"{'USED (bug)' if used else 'discarded'}")


if __name__ == "__main__":
    asyncio.run(main())
