"""LLM-as-judge — only where understanding is actually required.

Bias controls, all of them cheap:

  temperature 0
  anchored rubric — what a 3 looks like vs a 4, written down.
    Vague rubrics are where bias enters.
  compare against a REFERENCE, not in isolation. The question
    becomes "does this contain what the reference contains", so
    length stops being an advantage. This is the verbosity-bias fix.
  position swap — run both orderings on any pairwise comparison,
    count it only if the verdict agrees.

And the part worth being judged on: hand-label ~30 samples, measure
judge-human agreement (Cohen's kappa), report it alongside every
score. "Who judges the judge" deserves a number, not a shrug. If
agreement drops, the judge prompt is what is broken.

TODO(build): implement.
"""

from __future__ import annotations
