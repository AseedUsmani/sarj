"""Retry policy for upstream calls.

One policy, shared by every upstream, so behaviour cannot drift between them.

What is retried: connection-level failures only — a connect timeout, a read
timeout, a dropped connection, a pool timeout. These are transient and the
request never reached a decision.

What is not retried: anything the server answered. A 400, 401 or 404 will
return the same answer next time, and a 429 means the correct response is to
back off rather than to try again immediately.

All calls here are reads or stateless completions, so repeating one is safe.
Anything that changed state would need an idempotency key instead — see
bonus_assignment/docs/TDD.md.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable, TypeVar

import httpx

log = logging.getLogger("sarjy.retry")

T = TypeVar("T")

#: Transient by nature: the request did not reach a decision.
TRANSIENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


class RetryExhausted(RuntimeError):
    """Every attempt failed. Carries the real elapsed time and the last cause —
    never a configured constant, which sends you looking at the wrong setting."""

    def __init__(self, label: str, attempts: int, elapsed_ms: float, cause: Exception):
        self.label = label
        self.attempts = attempts
        self.elapsed_ms = elapsed_ms
        self.cause = cause
        super().__init__(
            f"{type(cause).__name__} calling {label} "
            f"after {attempts} attempt(s), {elapsed_ms:.0f}ms"
        )


async def call(
    fn: Callable[[], Awaitable[T]],
    *,
    label: str,
    attempts: int = 3,
    base_delay: float = 0.2,
    deadline_s: float = 8.0,
) -> T:
    """Run `fn`, retrying transient failures with exponential backoff.

    `deadline_s` bounds the whole sequence, and it matters more than `attempts`.
    Someone is waiting to hear an answer: three attempts against a blackholed
    host took 9.7s in testing, which is far worse than failing in two and saying
    so. Retries stop as soon as the budget is spent, whatever the attempt count.

    Backoff is 0.2s then 0.4s — deliberately short, for the same reason.
    """
    started = time.perf_counter()
    last: Exception = RuntimeError("no attempt made")

    for attempt in range(1, attempts + 1):
        # Check the budget before starting, not only before sleeping: an
        # attempt that begins at 3.1s with a 3s connect timeout blows a 5s
        # deadline on its own.
        if attempt > 1 and (time.perf_counter() - started) >= deadline_s:
            log.warning("%s giving up before attempt %d: %.1fs budget spent",
                        label, attempt, deadline_s)
            break
        try:
            return await fn()
        except TRANSIENT as exc:
            last = exc
            elapsed_s = time.perf_counter() - started
            log.warning(
                "%s attempt %d/%d failed after %.0fms: %s",
                label, attempt, attempts, elapsed_s * 1000, type(exc).__name__,
            )
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            if elapsed_s + delay >= deadline_s:
                log.warning("%s giving up: %.1fs budget spent", label, deadline_s)
                break
            await asyncio.sleep(delay)

    raise RetryExhausted(label, attempts, (time.perf_counter() - started) * 1000, last)
