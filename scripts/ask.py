"""Ask the agent one question and watch the loop work.

Real nodes, real LLM calls, real tools over MCP. Each step prints as
it happens: what the router understood, what each lap planned and
found, what Reflect decided, then the answer and its grounding check.

Run where the agent runs (the api container), with mcp-server up:

  python scripts/ask.py "Why did range drop on VIN-1042 this week?"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.agent.turn import run_turn  # noqa: E402
from ops_copilot.mcp_client.client import get_client  # noqa: E402
from ops_copilot.observability import tracing  # noqa: E402


def show(event: str, data: dict[str, Any]) -> None:
    if event == "routed":
        print(f"\nROUTER   domains={data['domains']} type={data['query_type']} "
              f"entities={data['entities']}" + (f"  notes={data['notes']}" if data["notes"] else ""))
    elif event == "plan":
        print(f"\nLAP {data['iteration']}  PLAN  {data['reasoning']}")
        for t in data["tools"]:
            arg = t["args"].get("sql") or t["args"].get("query")
            print(f"           -> {t['tool']}: {arg[:150]}{'...' if len(arg) > 150 else ''}")
    elif event == "evidence":
        for e in data["items"]:
            first = e["summary"].splitlines()[0]
            print(f"       OBSERVE [{e['id']}] {e['status']:<15} {first[:130]}")
    elif event == "reflect":
        verdict = data["stop_reason"] or "go again"
        extra = f"  missing={data['missing']}" if data["missing"] else ""
        extra += f"  next: {data['next_question']}" if data.get("next_question") else ""
        print(f"       REFLECT {verdict}{extra}")
    elif event == "node_error":
        print(f"       !! {data['node']} failed, fallback used: {data['error']}")
    elif event == "answer_reset":
        print("\n(grounding failed — rewriting the answer)")
    elif event == "groundedness":
        print(f"\nGROUNDING passed={data['passed']}"
              + (f" failures={data['tier_failures']}" if data["tier_failures"] else ""))


async def main(question: str) -> None:
    started = time.perf_counter()
    try:
        state = await run_turn(question, "ask-cli", turn_id=uuid.uuid4().hex, emit=show)
    finally:
        await get_client().close()
        tracing.shutdown()
    print(f"\nANSWER ({state.get('confidence')} confidence, {state.get('iteration')} lap(s), "
          f"stop: {state.get('stop_reason')}, {state.get('llm_calls')} LLM calls, "
          f"{time.perf_counter() - started:.1f}s)\n")
    print(state.get("answer"))
    if state.get("gaps"):
        print(f"\nGaps: {state['gaps']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("question")
    asyncio.run(main(p.parse_args().question))
