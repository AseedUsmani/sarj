"""Deferred questions.

When a turn cannot be answered because no location is known, the question is
held so the reply that supplies the location can also answer it. Without this,
the exchange runs:

    "should I take an umbrella tomorrow"  -> "where do you live?"
    "I live in Dubai"                     -> "Got it, I'll remember Dubai."
    "should I take an umbrella tomorrow"  -> (finally answered)

which makes the user repeat themselves — the most common complaint about
automated systems.

Deliberately small: one deferred question per session, in memory, short-lived.
It is a conversational convenience, not durable state, and it must not survive
long enough to answer a question the user has moved on from.
"""
import time
from dataclasses import dataclass
from typing import Optional

#: A deferred question is only worth answering while the user still has it in
#: mind. Two minutes is generous for a spoken exchange.
TTL_SECONDS = 120


@dataclass(frozen=True)
class Pending:
    text: str
    intent: str
    params: dict
    created_at: float

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > TTL_SECONDS


_pending: dict[str, Pending] = {}


def remember(session_id: str, text: str, intent: str, params: dict) -> None:
    _pending[session_id] = Pending(text, intent, dict(params), time.time())


def peek(session_id: str) -> bool:
    """Is a question waiting, without consuming it? The caller may still decide
    not to resume."""
    item = _pending.get(session_id)
    return item is not None and not item.expired


def take(session_id: str) -> Optional[Pending]:
    """Returns the deferred question and clears it. Single-shot: a question is
    answered once or not at all."""
    item = _pending.pop(session_id, None)
    if item is None or item.expired:
        return None
    return item


def clear(session_id: str) -> None:
    _pending.pop(session_id, None)


def size() -> int:
    return len(_pending)
