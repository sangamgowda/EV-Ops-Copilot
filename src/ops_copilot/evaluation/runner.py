"""Runs the golden set through the real pipeline, end to end.

Not a mock. The same graph that serves /chat serves this, or the
results mean nothing.

TODO(build): implement. Emit a regression report diffed against the
previous run — a pass rate with no comparison is not a regression
test.
"""

from __future__ import annotations


async def run_eval(golden_path: str) -> dict:
    raise NotImplementedError("see module docstring")
