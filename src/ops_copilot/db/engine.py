"""Two engines, deliberately.

  owner     migrations, ingestion, operational writes
  readonly  the SQL tool, and nothing else

They are separate objects so it is impossible to accidentally run a
generated query on the owner connection. The separation is
structural, not a convention someone has to remember.

The readonly pool is kept small on purpose: the agent should never
be able to exhaust connections the rest of the system needs.

TODO(build): implement with SQLAlchemy + psycopg pools.
"""

from __future__ import annotations
