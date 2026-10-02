"""What stands between the agent and a provider that is slow, full or down.

CircuitBreaker — after `failure_threshold` consecutive failures a model
is skipped for `cooldown_seconds`, so every call during an outage fails
over at once instead of each one waiting out its own retries. After the
cooldown one trial call is let through: success closes the breaker,
failure opens it again.

ModelQueue — every call to a model waits for a slot. A slot needs both
a free concurrency place and room in the model's tokens-per-minute
budget, so the provider's 429 is avoided rather than provoked. A call
that cannot get a slot within its wait limit gets QueueTimeout, which
the client treats like any other reason to try the fallback.

Both are per model id and per process, held in memory.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class QueueTimeout(Exception):
    """No slot for this model within the wait limit."""


class CircuitBreaker:
    def __init__(self, failure_threshold: int, cooldown_s: float) -> None:
        self.failure_threshold, self.cooldown_s = failure_threshold, cooldown_s
        self.failures = 0
        self.open_until = 0.0
        self._probing = False

    @property
    def state(self) -> str:
        if self.failures < self.failure_threshold:
            return "closed"
        return "open" if time.monotonic() < self.open_until else "half_open"

    def allow(self) -> bool:
        """May a call go to this model now? In half-open, only one may."""
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._probing:
            self._probing = True
            return True
        return False

    def success(self) -> None:
        self.failures, self._probing = 0, False

    def release(self) -> None:
        """The call ended without telling us anything about the model
        (a bad request, a full queue): free the trial slot, change nothing."""
        self._probing = False

    def failure(self) -> None:
        self._probing = False
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.open_until = time.monotonic() + self.cooldown_s


class ModelQueue:
    def __init__(self, max_concurrent: int, tokens_per_minute: int) -> None:
        self.tpm = tokens_per_minute
        self._slots = asyncio.Semaphore(max_concurrent)
        self._spent: deque[list[float]] = deque()   # [time, tokens], tokens corrected after the call
        self._budget_lock = asyncio.Lock()
        self.waiting = 0

    def _used(self, now: float) -> float:
        while self._spent and self._spent[0][0] <= now - 60:
            self._spent.popleft()
        return sum(t for _, t in self._spent)

    async def _reserve(self, tokens: int, deadline: float) -> list[float]:
        # A request larger than the whole budget could never fit; let it
        # through alone rather than wait forever (the provider decides).
        tokens = min(tokens, self.tpm)
        async with self._budget_lock:          # first come, first served
            while True:
                now = time.monotonic()
                if self._used(now) + tokens <= self.tpm:
                    entry = [now, float(tokens)]
                    self._spent.append(entry)
                    return entry
                wait = self._spent[0][0] + 60 - now
                if now + wait > deadline:
                    raise QueueTimeout(f"token budget full for {deadline - now:.0f}s+")
                await asyncio.sleep(wait)

    @asynccontextmanager
    async def slot(self, est_tokens: int, max_wait_s: float) -> AsyncIterator[list[float]]:
        """Yields the budget entry; set entry[1] to the real token count
        once known, so the budget reflects what was actually spent."""
        deadline = time.monotonic() + max_wait_s
        self.waiting += 1
        try:
            try:
                await asyncio.wait_for(self._slots.acquire(), timeout=max_wait_s)
            except TimeoutError as exc:
                raise QueueTimeout("no free slot") from exc
        finally:
            self.waiting -= 1
        try:
            entry = await self._reserve(est_tokens, deadline)
            yield entry
        finally:
            self._slots.release()
