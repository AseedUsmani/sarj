"""In-process similarity index.

The database is durability; this is the lookup. At the sizes involved a full
scan within one key namespace is sub-millisecond and — more importantly —
**exact**. The deep dive measures a false-hit rate, and an approximate index
would make a miss ambiguous between "the threshold rejected it" and "the index
did not find it" (docs/TDD.md §10.2).

Similarity here is character-trigram cosine, not a neural embedding. It resolves
phrasing variation inside a namespace where the intent and parameters already
match, which is a narrow enough job that a lexical measure does it. Swapping in
sentence embeddings means replacing `vectorise()` and bumping
`EMBEDDING_VERSION`; entries carrying an older version are skipped at load, so a
change empties the cache rather than corrupting it.
"""
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

EMBEDDING_VERSION = "trigram-v1"

_WORD = re.compile(r"[a-z0-9]+")


def _normalise(text: str) -> str:
    return " ".join(_WORD.findall((text or "").lower()))


def vectorise(text: str) -> dict[str, float]:
    """L2-normalised character-trigram counts over the normalised text."""
    s = f"  {_normalise(text)}  "
    counts: dict[str, float] = defaultdict(float)
    for i in range(len(s) - 2):
        counts[s[i:i + 3]] += 1.0
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
    return {k: v / norm for k, v in counts.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Both vectors are already L2-normalised, so the dot product is the cosine.
    Iterate the smaller side; these are sparse."""
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(k, 0.0) for k, w in a.items())


@dataclass
class Entry:
    key: str
    question: str
    answer: str
    expires_at: float
    vector: dict[str, float] = field(default_factory=dict)
    hits: int = 0
    row_id: Optional[int] = None

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class CacheIndex:
    """Exact key match first, similarity second and only within that namespace."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self._by_key: dict[str, list[Entry]] = defaultdict(list)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_key.values())

    @property
    def namespaces(self) -> int:
        return len([k for k, v in self._by_key.items() if v])

    def lookup(self, key: str, text: str) -> tuple[Optional[Entry], Optional[float]]:
        """Returns (entry_or_None, best_similarity_or_None).

        The similarity is returned even on a miss: the threshold sweep replays
        recorded scores rather than re-running the workload, so a miss must say
        how close it came.
        """
        bucket = self._by_key.get(key)
        if not bucket:
            return None, None

        live = [e for e in bucket if not e.expired]
        if len(live) != len(bucket):
            self._by_key[key] = live
        if not live:
            return None, None

        query = vectorise(text)
        best, score = None, -1.0
        for entry in live:
            s = cosine(query, entry.vector)
            if s > score:
                best, score = entry, s

        if best is not None and score >= self.threshold:
            best.hits += 1
            return best, round(score, 4)
        return None, round(score, 4)

    def add(self, entry: Entry) -> Entry:
        if not entry.vector:
            entry.vector = vectorise(entry.question)
        bucket = self._by_key[entry.key]
        # Replace a near-duplicate question rather than accumulating paraphrases
        # of the same thing; the namespace should stay small.
        for i, existing in enumerate(bucket):
            if cosine(entry.vector, existing.vector) >= 0.98:
                bucket[i] = entry
                return entry
        bucket.append(entry)
        return entry

    def purge_expired(self) -> int:
        removed = 0
        for key, bucket in list(self._by_key.items()):
            live = [e for e in bucket if not e.expired]
            removed += len(bucket) - len(live)
            if live:
                self._by_key[key] = live
            else:
                del self._by_key[key]
        return removed

    def clear(self) -> int:
        n = len(self)
        self._by_key.clear()
        return n

    def stats(self) -> dict:
        return {
            "entries": len(self),
            "namespaces": self.namespaces,
            "hits": sum(e.hits for v in self._by_key.values() for e in v),
            "threshold": self.threshold,
            "embedding_version": EMBEDDING_VERSION,
        }
