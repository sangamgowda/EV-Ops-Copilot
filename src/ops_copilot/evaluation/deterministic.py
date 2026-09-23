"""Evaluators that need no LLM. Fast, free, never flaky.

  domain set match          did the router get domains[] right
  tool selection F1         right tools, set-compared
  tool argument match       key args after normalisation
  iteration count in bound  did it loop more than it needed to
  stop_reason match         did it stop for the right reason
  numeric facts present     within tolerance
  citations resolve         every cited id exists
  must_not_contain absent   e.g. a ruled-out cause not asserted

Anything checkable in code is checked in code. The judge only gets
what genuinely needs reading, which shrinks the surface for judge
bias considerably.

TODO(build): implement each as a pure function.
"""

from __future__ import annotations
