"""The classifier contract.

Understanding a request is an AI/DS responsibility. This service depends on this
interface, not on an implementation — whether it is rules, a nearest-neighbour
index or a fine-tuned model is theirs to decide and change.

What the backend owns and states here: the shape, the latency budget, the
determinism requirement, and the version, because the version enters the cache
key (docs/TDD.md §4).
"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Classification:
    intent: str
    params: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    model_version: str = "unknown"


@runtime_checkable
class Classifier(Protocol):
    #: Enters the cache key. Must change whenever behaviour changes, so a
    #: deploy writes into a fresh namespace instead of serving entries under
    #: semantics that no longer hold.
    version: str

    def classify(self, text: str) -> Classification:
        """Must be deterministic within a version. Budget: p99 <= 20ms."""
        ...
