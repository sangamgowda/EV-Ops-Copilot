"""Provider-agnostic LLM wrapper.

Groq is OpenAI-compatible, so one client covers both and swapping
providers is a base-URL change rather than a rewrite. That is the
whole reason this file exists instead of calling the SDK directly
from each node.

Free-tier reality worth designing around: the strong model has a
materially smaller daily token budget than the cheap one (model ids
live in .env — providers retire them, so they are config, not code).
Model tiering is
not only a cost optimisation here — it is what keeps the system
inside the free tier at all. Router and Reflect on the cheap model
means the expensive budget is spent only on Plan and Synthesize.

Handles: tier resolution, JSON-mode responses, streaming for
synthesis, retry with backoff, and 429 handling that respects the
retry-after header rather than guessing.

On 429s: the OpenAI SDK already honours `retry-after` (and
`retry-after-ms`) and backs off exponentially when the header is
absent, for up to `llm_max_retries` attempts. Re-implementing that
here would only add a second, disagreeing retry loop.

Structured output is never parsed from free text. Every JSON call is
validated against a Pydantic model; a response that fails validation
gets exactly one repair attempt with the validation error shown to
the model, then the error propagates.
"""

from __future__ import annotations

import functools
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from ops_copilot.observability.tracing import record_generation
from ops_copilot.settings import get_config, get_settings, model_for

T = TypeVar("T", bound=BaseModel)


class LLMNotConfigured(RuntimeError):
    pass


class LLMOutputError(RuntimeError):
    """The model's output could not be validated, even after repair."""


@dataclass
class StreamResult:
    """Filled in as a stream is consumed; complete once it ends."""
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)


@functools.lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    s = get_settings()
    if not s.groq_api_key:
        raise LLMNotConfigured("GROQ_API_KEY is not set; put a key in .env")
    return AsyncOpenAI(
        api_key=s.groq_api_key,
        base_url=s.llm_base_url,
        timeout=s.llm_timeout_seconds,
        max_retries=s.llm_max_retries,
    )


def _params(node: str) -> dict:
    cfg = get_config()["llm"]
    params = {
        "model": model_for(node),
        "temperature": cfg["temperature"].get(node, 0.0),
        "max_tokens": cfg["max_tokens"].get(node, 1024),
    }
    # Reasoning models think before answering, and the thinking counts
    # against max_tokens. Keeping it short keeps replies inside the
    # budget and latency down. Sent as extra_body: providers that do
    # not support it are configured without the key.
    effort = cfg.get("reasoning_effort", {}).get(node)
    if effort:
        params["extra_body"] = {"reasoning_effort": effort}
    return params


def extract_json(text: str) -> str:
    # JSON mode should make fences impossible; smaller models add
    # them anyway often enough to be worth one line.
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return m.group(1) if m else text


def _messages(system: str, user: str, json_mode: bool) -> list[dict[str, str]]:
    # OpenAI-compatible providers reject JSON mode unless the word "json"
    # appears in the messages. Guaranteed here, so a prompt file that
    # shows the shape without naming it cannot break its node.
    if json_mode and "json" not in (system + user).lower():
        system = f"{system}\n\nRespond with a single JSON object."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _usage(resp_usage: object) -> dict[str, int]:
    if resp_usage is None:
        return {}
    return {
        "input": getattr(resp_usage, "prompt_tokens", 0) or 0,
        "output": getattr(resp_usage, "completion_tokens", 0) or 0,
    }


async def complete(node: str, system: str, user: str, *, json_mode: bool = False) -> str:
    params = _params(node)
    messages = _messages(system, user, json_mode)
    started = time.perf_counter()
    resp = await _client().chat.completions.create(
        messages=messages,
        response_format={"type": "json_object"} if json_mode else None,
        **params,
    )
    text = resp.choices[0].message.content or ""
    record_generation(node, params["model"], messages, text, _usage(resp.usage),
                      int((time.perf_counter() - started) * 1000))
    return text


async def complete_json(node: str, system: str, user: str, schema: type[T]) -> T:
    repairs = get_config()["llm"]["json_repair_attempts"]
    prompt = user
    last_error = ""
    for _ in range(repairs + 1):
        raw = await complete(node, system, prompt, json_mode=True)
        try:
            return schema.model_validate(json.loads(extract_json(raw)))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            prompt = (
                f"{user}\n\nYour previous reply was:\n{raw}\n\n"
                f"It failed validation:\n{last_error}\n\n"
                "Reply again with ONLY a corrected JSON object."
            )
    raise LLMOutputError(f"{node}: output failed validation after repair: {last_error}")


async def stream(node: str, system: str, user: str, result: StreamResult, *,
                 json_mode: bool = False) -> AsyncIterator[str]:
    """Yield text deltas as they arrive. `result` holds the full text
    and token usage once the iterator is exhausted."""
    params = _params(node)
    messages = _messages(system, user, json_mode)
    started = time.perf_counter()
    resp = await _client().chat.completions.create(
        messages=messages,
        response_format={"type": "json_object"} if json_mode else None,
        stream=True,
        stream_options={"include_usage": True},
        **params,
    )
    parts: list[str] = []
    async for chunk in resp:
        if chunk.usage is not None:
            result.usage = _usage(chunk.usage)
        if chunk.choices and chunk.choices[0].delta.content:
            delta = chunk.choices[0].delta.content
            parts.append(delta)
            yield delta
    result.text = "".join(parts)
    record_generation(node, params["model"], messages, result.text, result.usage,
                      int((time.perf_counter() - started) * 1000))
