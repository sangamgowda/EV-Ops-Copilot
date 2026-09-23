"""Seed a realistic dataset. This is the demo's foundation.

Because the data is generated here, ground truth is KNOWN — which
is what makes most of the golden set possible without hand-labelling
every case.

What to seed, and why each part matters:

  vehicles            a few hundred across 3 model codes and
                      several regions
  baselines           nominal current draw, range, and payload per
                      model and drive mode. WITHOUT THESE THERE IS
                      NO DIAGNOSIS — only a reading with nothing to
                      compare it to.
  telemetry           90 days, several metrics, realistic daily
                      variation
  planted faults      a handful of vehicles with a DELIBERATE,
                      explainable story: elevated current draw plus
                      payload above rating, with normal cell health.
                      This is the case the three-lap demo walks
                      through, and it must be real in the data, not
                      narrated.
  sales               2 years, seasonal, regional variation
  documents           service bulletins (one of which explains the
                      planted fault), an error-code table to be
                      promoted, and success stories for the
                      business domain
  a gap               at least one model with telemetry but NO
                      documentation, so the partial-answer path is
                      demonstrable rather than theoretical

Usage:  python scripts/seed_synthetic_data.py --vehicles 300 --days 90

TODO(build): implement.
"""

from __future__ import annotations
