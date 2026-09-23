"""Capture a rating against a turn.

Nothing special is recorded at this moment — the full trace already
exists. The rating just marks it.

Also captures IMPLICIT negatives: a user rephrasing the same
question within implicit_negative_window_seconds. There are far
more of these than explicit thumbs-down, and they are the same
signal.

TODO(build): implement.
"""

from __future__ import annotations
