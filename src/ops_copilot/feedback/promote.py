"""Turn failures into regression tests. Offline, nightly.

  1. pull recent down-rated turns
  2. cluster by FAILURE SIGNATURE, read off trace fields in code —
     router domain mismatch / wrong tool / empty retrieval /
     groundedness failure / cap reached / correct but slow.
     Deterministic bucketing, no LLM.
  3. sort the queue by cluster size — fix what is frequent, not
     what was loudest
  4. a HUMAN writes what should have happened. This step is not
     automatable and pretending otherwise is the mistake: a
     thumbs-down can mean the user was wrong, or wanted a different
     format. Auto-promoting raw failures teaches the eval set to
     expect the same mistake.
  5. emit a golden sample tagged source=promoted,
     regression_for=<cluster_id>
  6. CI runs the suite on every prompt or config change; a promoted
     case regressing blocks the merge

Track cluster resolution rate, not just case count — did the fix
clear the whole cluster or only that one case?

TODO(build): implement.
"""

from __future__ import annotations
