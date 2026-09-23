"""rag_retrieval_tool — written explanation from documents.

Fixed pipeline, same every call: hybrid search -> filter/boost ->
rerank -> threshold. Nothing here decides anything; the decision
happened in Plan.

Returns chunks with rerank scores and source documents — or an
explicit EMPTY result when nothing clears the confidence threshold.
The empty result is the important case: it becomes evidence, which
is what lets the agent say "I have no documentation on this"
instead of inventing a mechanism.

TODO(build): implement, delegating to rag.retrieve.
"""

from __future__ import annotations


async def rag_retrieval(query: str, domain: str, entity_id: str | None = None) -> dict:
    raise NotImplementedError("see module docstring")
