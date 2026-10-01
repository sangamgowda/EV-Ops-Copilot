"""Who may call what.

/ingest changes what the agent reads and /eval spends the model
budget, so both need the admin token (`Authorization: Bearer ...`).
With no ADMIN_TOKEN configured they refuse everyone (503): an unset
secret must never mean "open".

/chat is open but rate-limited per client address. The limiter is in
memory, so it holds per process: enough for one container, not a
substitute for a gateway in front of several.
"""

from __future__ import annotations

import hmac
import math
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from ops_copilot.settings import get_config, get_settings


def require_admin(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(503, "admin endpoints are disabled: set ADMIN_TOKEN")
    scheme, _, given = (authorization or "").partition(" ")
    # Constant-time compare, so response timing does not leak the token.
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "admin token required", headers={"WWW-Authenticate": "Bearer"})


class SlidingWindowLimiter:
    """At most `limit` calls per `window_s` seconds for each key."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit, self.window_s = limit, window_s
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> float | None:
        """None if allowed (and counted), else seconds until a slot frees."""
        now = time.monotonic() if now is None else now
        calls = self._calls[key]
        while calls and calls[0] <= now - self.window_s:
            calls.popleft()
        if len(calls) >= self.limit:
            return calls[0] + self.window_s - now
        calls.append(now)
        return None


_chat_limiter: SlidingWindowLimiter | None = None


def chat_rate_limit(request: Request) -> None:
    global _chat_limiter
    limit = get_config()["api"]["chat_rate_limit_per_minute"]
    if _chat_limiter is None or _chat_limiter.limit != limit:
        _chat_limiter = SlidingWindowLimiter(limit)
    wait = _chat_limiter.check(request.client.host if request.client else "unknown")
    if wait is not None:
        raise HTTPException(429, "too many questions; try again shortly",
                            headers={"Retry-After": str(max(1, math.ceil(wait)))})
