"""Retry with exponential backoff for model calls.

Why hand-roll this instead of leaning on the OpenAI SDK's max_retries?

* The SDK only retries the request that creates a response. A stream that dies
  halfway through has already been handed back to us, so the SDK never sees it.
  We retry the "open the stream" step ourselves, before any token is consumed.
* models.list() (model discovery) deliberately runs with max_retries=0 and a
  short timeout so login stays snappy; it still deserves a cheap retry.
* The user should see what is happening: every retry is surfaced through the UI
  (on_retry) instead of silently sleeping inside the HTTP layer.
* Cancellation (ctrl+c) must win over backoff, so should_stop is checked before
  each retry.

The SDK retries stay enabled; the two layers compose. Everything that touches
time or side effects is injectable so behaviour is unit-testable without real
sleeping.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")

#: HTTP status codes that are worth retrying (transient server/limit errors).
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

#: Exception class names (SDK or otherwise) that signal a transient failure.
RETRYABLE_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "APIConnectionTimeoutError",
    "InternalServerError",
    "RateLimitError",
    "ServiceUnavailableError",
}

#: Substrings (lowercase) that mark a transient failure in the message text.
RETRYABLE_MESSAGES = (
    "rate limit",
    "overloaded",
    "temporarily unavailable",
    "connection reset",
    "timed out",
)


def _status_of(exc: Exception) -> int | None:
    try:
        status = getattr(exc, "status_code", None)
    except Exception:  # a property may raise; treat that as "no status"
        return None
    if isinstance(status, bool):
        return None
    if isinstance(status, int):
        return status
    if isinstance(status, str) and status.isdigit():
        return int(status)
    return None


def _message_of(exc: Exception) -> str:
    try:
        message = getattr(exc, "message", None) or str(exc)
    except Exception:
        return ""
    return str(message).lower()


def is_retryable(exc: Exception) -> bool:
    """True when exc looks like a transient failure worth another try."""
    status = _status_of(exc)
    if status is not None and status in RETRYABLE_STATUS:
        return True
    for klass in type(exc).__mro__:
        if klass.__name__ in RETRYABLE_NAMES:
            return True
    message = _message_of(exc)
    return any(token in message for token in RETRYABLE_MESSAGES)


@dataclass
class RetryPolicy:
    """How many times to try, and how long to wait between attempts."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0
    jitter: float = 0.25  # +/- 25% random spread so retries do not sync up

    def delay_for(self, attempt: int) -> float:
        """Backoff before the next attempt, for the attempt that just failed."""
        exponent = max(0, int(attempt) - 1)
        try:
            delay = float(self.base_delay) * (2 ** exponent)
        except OverflowError:  # absurd attempt count: just use the ceiling
            delay = float(self.max_delay)
        ceiling = float(self.max_delay)
        if ceiling >= 0:
            delay = min(delay, ceiling)
        if self.jitter:
            delay *= 1.0 + random.uniform(-self.jitter, self.jitter)
        return max(0.0, delay)


def retry_call(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy = RetryPolicy(),
    on_retry: Callable[[Exception, int, float], Any] | None = None,
    should_stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], Any] = time.sleep,
    on_attempt: Callable[[int], Any] | None = None,
) -> T:
    """Call fn and retry transient failures according to policy.

    attempt counts from 1. on_attempt runs before every attempt, on_retry runs
    after a failure that will be retried. Non-retryable failures, an exhausted
    policy, or a true should_stop() re-raise the original exception unchanged.
    """
    attempt = 1
    while True:
        if on_attempt is not None:
            try:
                on_attempt(attempt)
            except Exception:
                pass
        try:
            return fn()
        except Exception as exc:
            if should_stop is not None and should_stop():
                raise
            if not is_retryable(exc):
                raise
            if attempt >= int(policy.max_attempts):
                raise
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                try:
                    on_retry(exc, attempt, delay)
                except Exception:
                    pass  # UI/reporting problems must never break a retry
            sleep(delay)
            attempt += 1
