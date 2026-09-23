"""Run the golden set and print a regression report.

Usage:  python scripts/run_eval.py [--golden path] [--compare-to run_id]

Exits non-zero when a previously-passing case fails, so CI can gate
merges on it.

TODO(build): implement.
"""

from __future__ import annotations
