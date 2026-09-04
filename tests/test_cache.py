"""Cache correctness.

The claim the deep dive rests on is that the *key* provides safety and the
*threshold* only provides quality. These tests assert exactly that, and the
important one is `test_namespace_isolation_at_zero`: with the similarity
threshold set to 0.0 — where every stored entry looks like a perfect match —
a request must still never receive an entry from another namespace.

If that holds at 0.0, separation is structural rather than a consequence of
tuning, and no threshold change can introduce a cross-parameter false hit.

No network, no model. Runs in milliseconds.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cache.index import CacheIndex, Entry, cosine, vectorise
from app.cache.keys import build_key

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def entry(key: str, question: str, answer: str, ttl: int = 600) -> Entry:
    return Entry(key=key, question=question, answer=answer,
                 expires_at=time.time() + ttl, vector=vectorise(question))


# ── keys ──────────────────────────────────────────────────────────────────
def test_keys():
    V = "rules-v1"
    same_a = build_key(V, "current_weather", {"city": "delhi"})
    same_b = build_key(V, "current_weather", {"city": "Delhi "})
    check(same_a == same_b, f"case/whitespace should normalise: {same_a} != {same_b}")

    # Parameter order must not produce two keys for one request.
    o1 = build_key(V, "rain_forecast", {"city": "delhi", "day": "today"})
    o2 = build_key(V, "rain_forecast", {"day": "today", "city": "delhi"})
    check(o1 == o2, "parameter order changed the key")

    # Different parameter -> different namespace. This is the safety property.
    check(build_key(V, "current_weather", {"city": "delhi"})
          != build_key(V, "current_weather", {"city": "mumbai"}),
          "Delhi and Mumbai share a key")
    check(build_key(V, "rain_forecast", {"city": "delhi", "day": "today"})
          != build_key(V, "rain_forecast", {"city": "delhi", "day": "tomorrow"}),
          "today and tomorrow share a key")
    check(build_key(V, "temperature", {"city": "delhi"})
          != build_key(V, "humidity", {"city": "delhi"}),
          "different intents share a key")

    # A classifier deploy must write into a fresh namespace.
    check(build_key("rules-v1", "current_weather", {"city": "delhi"})
          != build_key("rules-v2", "current_weather", {"city": "delhi"}),
          "classifier version is not in the key")

    # `unit` changes rendering, not the answer, so it must not fragment.
    check(build_key(V, "temperature", {"city": "delhi", "unit": "celsius"})
          == build_key(V, "temperature", {"city": "delhi", "unit": "fahrenheit"}),
          "unit fragmented the namespace")


# ── the gate that matters ─────────────────────────────────────────────────
def test_namespace_isolation_at_zero():
    """At threshold 0.0 every entry is a match on similarity alone. Anything
    returned across namespaces here would be a false hit that no threshold
    could prevent."""
    idx = CacheIndex(threshold=0.0)
    V = "rules-v1"
    idx.add(entry(build_key(V, "current_weather", {"city": "delhi"}),
                  "What's the weather in Delhi?", "Delhi: 28C"))
    idx.add(entry(build_key(V, "current_weather", {"city": "mumbai"}),
                  "What's the weather in Mumbai?", "Mumbai: 27C"))
    idx.add(entry(build_key(V, "rain_forecast", {"city": "delhi", "day": "tomorrow"}),
                  "Will it rain in Delhi tomorrow?", "Delhi tomorrow: 5%"))

    hit, sim = idx.lookup(build_key(V, "current_weather", {"city": "mumbai"}),
                          "What's the weather in Mumbai?")
    check(hit is not None and hit.answer == "Mumbai: 27C",
          "Mumbai lookup did not return the Mumbai entry")

    # A namespace with nothing in it must miss, not fall back to a neighbour.
    hit, sim = idx.lookup(build_key(V, "current_weather", {"city": "chennai"}),
                          "What's the weather in Chennai?")
    check(hit is None, "empty namespace returned an entry from another")
    check(sim is None, "a namespace miss should record no similarity at all")

    hit, _ = idx.lookup(build_key(V, "rain_forecast", {"city": "delhi", "day": "today"}),
                        "Will it rain in Delhi today?")
    check(hit is None, "today was served tomorrow's answer")


def test_similarity_cannot_separate_answers():
    """The measured claim: paraphrases can score lower than different-answer
    pairs, so similarity alone is not a safe test of 'same answer'."""
    para = cosine(vectorise("What's the weather in Delhi?"),
                  vectorise("How's Delhi looking?"))
    diff = cosine(vectorise("What's the weather in Delhi?"),
                  vectorise("What's the weather in Mumbai?"))
    check(diff > para,
          f"expected the overlap to hold: paraphrase {para:.3f} vs "
          f"different-city {diff:.3f}")


# ── behaviour ─────────────────────────────────────────────────────────────
def test_threshold_gates_within_namespace():
    key = build_key("rules-v1", "current_weather", {"city": "delhi"})
    strict = CacheIndex(threshold=0.95)
    strict.add(entry(key, "What's the weather in Delhi?", "28C"))
    hit, sim = strict.lookup(key, "How's Delhi looking?")
    check(hit is None, "a distant phrasing hit at threshold 0.95")
    check(sim is not None, "a threshold miss must still record the score")

    loose = CacheIndex(threshold=0.20)
    loose.add(entry(key, "What's the weather in Delhi?", "28C"))
    hit, _ = loose.lookup(key, "How's Delhi looking?")
    check(hit is not None, "the same phrasing missed at threshold 0.20")


def test_expiry():
    key = build_key("rules-v1", "current_weather", {"city": "delhi"})
    idx = CacheIndex(threshold=0.2)
    idx.add(entry(key, "weather in Delhi", "28C", ttl=-1))
    hit, _ = idx.lookup(key, "weather in Delhi")
    check(hit is None, "an expired entry was served")
    check(len(idx) == 0, "expired entry was not evicted on lookup")


def test_hit_counter_and_flush():
    key = build_key("rules-v1", "current_weather", {"city": "delhi"})
    idx = CacheIndex(threshold=0.2)
    idx.add(entry(key, "weather in Delhi", "28C"))
    for _ in range(3):
        idx.lookup(key, "weather in Delhi")
    check(idx.stats()["hits"] == 3, f"hit counter wrong: {idx.stats()['hits']}")
    check(idx.clear() == 1, "clear did not report the entry it removed")
    check(len(idx) == 0, "clear left entries behind")


def test_near_duplicate_replaces():
    """A namespace should not accumulate near-identical phrasings."""
    key = build_key("rules-v1", "current_weather", {"city": "delhi"})
    idx = CacheIndex(threshold=0.2)
    idx.add(entry(key, "weather in Delhi", "old"))
    idx.add(entry(key, "weather in Delhi", "new"))
    check(len(idx) == 1, f"near-duplicate was stored separately: {len(idx)} entries")
    hit, _ = idx.lookup(key, "weather in Delhi")
    check(hit.answer == "new", "the newer answer did not replace the older one")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    for f in failures:
        print("FAIL", f)
    print(f"\n{'PASS' if not failures else 'FAILED'}: "
          f"{len(tests) - len({f for f in failures})} of {len(tests)} test "
          f"functions clean, {len(failures)} assertion failure(s)")
    sys.exit(1 if failures else 0)
