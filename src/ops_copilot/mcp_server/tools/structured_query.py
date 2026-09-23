"""structured_query_tool — precise values from the database.

Takes SQL the model already wrote (Plan generates it against the
injected schema config, in the same call that selects the tool).
This tool does not generate anything.

Sequence, none of it involving an LLM:

  1. SQLValidator.validate  — AST checks, LIMIT injection
  2. SQLValidator.explain_gate — planner cost, before execution
  3. execute on the READ REPLICA as the READ-ONLY role, with a
     statement timeout

Three independent layers. AST catches queries that should not
exist. EXPLAIN catches queries that are valid and ruinous. The
read-only role catches whatever the first two missed, because it
is structurally incapable of writing.

Returns rows plus provenance (the SQL that ran, the EXPLAIN cost,
latency) so the trace shows exactly what happened.

TODO(build): implement.
"""

from __future__ import annotations


async def structured_query(sql: str) -> dict:
    raise NotImplementedError("see module docstring")
