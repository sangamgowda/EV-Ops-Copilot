"""Run the agent loop once with the Phase 3 skeleton nodes.

No LLM, no database, no MCP: every node returns a fixed value. What
this shows is the SHAPE — the backward edge loops, evidence accumulates
lap over lap, and the loop stops. Each node logs one line as it runs.

Usage:  python scripts/run_skeleton.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.agent.graph import build_graph  # noqa: E402
from ops_copilot.agent.state import new_state  # noqa: E402


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    state = await build_graph().ainvoke(
        new_state("Why did range drop on VIN-1042 last week?", "skeleton-session", "turn-1")
    )
    print("\n--- result ---")
    print(f"laps:        {state['iteration']}")
    print(f"stop reason: {state['stop_reason']}")
    print(f"evidence:    {[(e.id, e.iteration) for e in state['evidence']]}  (id, lap)")
    print(f"answer:      {state['answer']}")


if __name__ == "__main__":
    asyncio.run(main())
