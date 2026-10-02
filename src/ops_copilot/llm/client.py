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

Reliability (llm/resilience.py): every call waits for a slot in its
model's queue (concurrency + tokens-per-minute budget), and each model
has a circuit breaker. When the primary model is rate-limited, timing
out, erroring or cooling down, the call goes to the fallback model for
its tier (LLM_FALLBACK_* in .env). Evaluation switches the fallback off
with `primary_only()`, so a score always belongs to the models it names.
A stream fails over only before its first token.

Structured output is never parsed from free text. Every JSON call is
validated against a Pydantic model; a response that fails validation
gets exactly one repair attempt with the validation error shown to
the model, then the error propagates.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from ops_copilot.llm.resilience import CircuitBreaker, ModelQueue, QueueTimeout
from ops_copilot.observability.tracing import record_generation
from ops_copilot.settings import get_config, get_settings, model_for, tier_for

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


class LLMNotConfigured(RuntimeError):
    pass


class LLMOutputError(RuntimeError):
    """The model's output could not be validated, even after repair."""


class LLMUnavailable(RuntimeError):
    """Every model for this call is failing, cooling down or saturated."""


@dataclass
class StreamResult:
    """Filled in as a stream is consumed; complete once it ends."""
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)


# ── fail-over ────────────────────────────────────────────────

# Worth trying another model for: the provider is full, slow or down.
# A 400 (bad request) is not — the same request fails anywhere.
_FAILOVER_ERRORS = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                    openai.InternalServerError, QueueTimeout)


def _is_failover(exc: BaseException) -> bool:
    # 413 "Request too large" is a per-model limit; another model may accept it.
    return isinstance(exc, _FAILOVER_ERRORS) or getattr(exc, "status_code", None) == 413


_fallback_allowed: contextvars.ContextVar[bool] = contextvars.ContextVar("llm_fallback", default=True)


@contextmanager
def primary_only() -> Iterator[None]:
    """No fallback inside this block (evaluation: a score must belong to
    the models the report names)."""
    token = _fallback_allowed.set(False)
    try:
        yield
    finally:
        _fallback_allowed.reset(token)


@dataclass(frozen=True)
class Target:
    role: str          # "primary" or "fallback"; also names the client
    model: str


_breakers: dict[str, CircuitBreaker] = {}
_queues: dict[str, ModelQueue] = {}


def breaker(model: str) -> CircuitBreaker:
    if model not in _breakers:
        cfg = get_config()["llm"]["circuit_breaker"]
        _breakers[model] = CircuitBreaker(cfg["failure_threshold"], cfg["cooldown_seconds"])
    return _breakers[model]


def queue(model: str) -> ModelQueue:
    if model not in _queues:
        cfg = get_config()["llm"]["queue"]
        tpm = (cfg.get("tokens_per_minute_overrides") or {}).get(model, cfg["tokens_per_minute"])
        _queues[model] = ModelQueue(cfg["max_concurrent_per_model"], tpm)
    return _queues[model]


@functools.lru_cache(maxsize=2)
def _client(role: str = "primary") -> AsyncOpenAI:
    s = get_settings()
    key, url = s.groq_api_key, s.llm_base_url
    if role == "fallback":
        # Empty means "same provider as the primary": on a free tier each
        # model has its own allowance, so another model there is a real
        # fallback.
        key, url = s.llm_fallback_api_key or key, s.llm_fallback_base_url or url
    if not key:
        raise LLMNotConfigured("GROQ_API_KEY is not set; put a key in .env")
    return AsyncOpenAI(api_key=key, base_url=url, timeout=s.llm_timeout_seconds,
                       max_retries=s.llm_max_retries)


def _targets(node: str) -> list[Target]:
    targets = [Target("primary", model_for(node))]
    if _fallback_allowed.get():
        s = get_settings()
        fb = {"strong": s.llm_fallback_model_strong, "cheap": s.llm_fallback_model_cheap}.get(tier_for(node))
        if fb and fb != targets[0].model:
            targets.append(Target("fallback", fb))
    return targets


def _params(node: str, model: str | None = None) -> dict:
    cfg = get_config()["llm"]
    model = model or model_for(node)
    params = {
        "model": model,
        "temperature": cfg["temperature"].get(node, 0.0),
        "max_tokens": cfg["max_tokens"].get(node, 1024),
    }
    # Reasoning models think before answering, and the thinking counts
    # against max_tokens. Keeping it short keeps replies inside the
    # budget and latency down. Sent only to models that accept it: a
    # fallback model without reasoning rejects the field outright.
    effort = cfg.get("reasoning_effort", {}).get(node)
    if effort and any(m in model for m in cfg.get("reasoning_effort_models", [])):
        params["extra_body"] = {"reasoning_effort": effort}
    return params


def _estimate_tokens(messages: list[dict[str, str]], max_tokens: int) -> int:
    # ~4 characters a token for English; output rarely reaches its cap.
    est_out = get_config()["llm"]["queue"]["estimated_output_tokens"]
    return sum(len(m["content"]) for m in messages) // 4 + min(max_tokens, est_out)


async def _with_failover(node: str, messages: list[dict[str, str]],
                         call: Callable[[AsyncOpenAI, dict], Awaitable[tuple[R, dict[str, int]]]]) -> R:
    """Run `call` on the first target that is allowed and answers.
    `call` returns (result, usage); usage corrects the queue's budget."""
    cfg = get_config()["llm"]["queue"]
    targets = _targets(node)
    last: BaseException | None = None
    for i, target in enumerate(targets):
        cb = breaker(target.model)
        if not cb.allow():
            log.info("%s: %s breaker open, skipping", node, target.model)
            continue
        params = _params(node, target.model)
        # With a fallback still to try, do not queue long for this one.
        wait = cfg["max_wait_seconds"] if i == len(targets) - 1 else cfg["failover_wait_seconds"]
        try:
            async with queue(target.model).slot(_estimate_tokens(messages, params["max_tokens"]),
                                                wait) as entry:
                result, usage = await call(_client(target.role), params)
                if usage:
                    entry[1] = float(usage.get("input", 0) + usage.get("output", 0))
        except Exception as exc:
            if not _is_failover(exc):
                cb.release()
                raise
            if not isinstance(exc, QueueTimeout):     # a full queue is not the model failing
                cb.failure()
            else:
                cb.release()
            last = exc
            log.warning("%s: %s failed (%s); %s", node, target.model, type(exc).__name__,
                        "trying fallback" if i < len(targets) - 1 else "no fallback left")
            continue
        cb.success()
        if target.role == "fallback":
            log.warning("%s answered by fallback model %s", node, target.model)
        return result
    if last is not None and not _fallback_allowed.get():
        raise last   # evaluation sees the provider's own error; its rate-limit watch reads it
    states = ", ".join(f"{t.model}={breaker(t.model).state}" for t in targets)
    raise LLMUnavailable(f"{node}: no model available ({states})") from last


# ── calls ────────────────────────────────────────────────────

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
    messages = _messages(system, user, json_mode)

    async def call(client: AsyncOpenAI, params: dict) -> tuple[str, dict[str, int]]:
        started = time.perf_counter()
        resp = await client.chat.completions.create(
            messages=messages,
            response_format={"type": "json_object"} if json_mode else None,
            **params,
        )
        text = resp.choices[0].message.content or ""
        usage = _usage(resp.usage)
        record_generation(node, params["model"], messages, text, usage,
                          int((time.perf_counter() - started) * 1000))
        return text, usage

    return await _with_failover(node, messages, call)


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
    messages = _messages(system, user, json_mode)
    started = time.perf_counter()

    async def open_stream(client: AsyncOpenAI, params: dict) -> tuple[tuple[Any, str], dict[str, int]]:
        # Fail-over covers opening the stream. Once tokens have reached
        # the user, switching models would splice two answers together.
        resp = await client.chat.completions.create(
            messages=messages,
            response_format={"type": "json_object"} if json_mode else None,
            stream=True,
            stream_options={"include_usage": True},
            **params,
        )
        return (resp, params["model"]), {}

    resp, model = await _with_failover(node, messages, open_stream)
    parts: list[str] = []
    async for chunk in resp:
        if chunk.usage is not None:
            result.usage = _usage(chunk.usage)
        if chunk.choices and chunk.choices[0].delta.content:
            delta = chunk.choices[0].delta.content
            parts.append(delta)
            yield delta
    result.text = "".join(parts)
    record_generation(node, model, messages, result.text, result.usage,
                      int((time.perf_counter() - started) * 1000))
