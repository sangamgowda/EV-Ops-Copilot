"""Provider-agnostic LLM wrapper.

Groq is OpenAI-compatible, so one client covers both and swapping
providers is a base-URL change rather than a rewrite. That is the
whole reason this file exists instead of calling the SDK directly
from each node.

Free-tier reality worth designing around: the strong model
(llama-3.3-70b-versatile) has a materially smaller daily token
budget than the cheap one (llama-3.1-8b-instant). Model tiering is
not only a cost optimisation here — it is what keeps the system
inside the free tier at all. Router and Reflect on the cheap model
means the expensive budget is spent only on Plan and Synthesize.

Handles: tier resolution, JSON-mode responses, streaming for
synthesis, retry with backoff, and 429 handling that respects the
retry-after header rather than guessing.

TODO(build): implement complete(), complete_json(), stream().
"""

from __future__ import annotations


async def complete_json(node: str, system: str, user: str, schema: type) -> dict:
    raise NotImplementedError("see module docstring")
