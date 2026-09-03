"""Tool contract.

Each tool declares its own metadata rather than the pipeline hard-coding
assumptions about it. Freshness in particular belongs with the data source: the
cache asks the tool how long an answer stays true, which is what lets a
different deployment swap TTL for event-driven invalidation without touching
cache code (docs/TDD.md).
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ToolSpec:
    name: str
    freshness_seconds: int
    timeout_s: float
    read_only: bool = True          # nothing here changes state


@dataclass
class ToolResult:
    ok: bool
    #: Plain-text facts injected into the prompt. Never assembled by the model.
    context: str = ""
    #: Why it failed, for the log. Never shown to the user verbatim.
    error: Optional[str] = None
    #: Overrides the spec default when the data itself implies a window.
    freshness_seconds: Optional[int] = None
    meta: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.ok and bool(self.context)
