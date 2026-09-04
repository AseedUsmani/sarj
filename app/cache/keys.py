"""Cache key construction.

A key encodes the *identity* of a request, not its surface form. Two requests
with different parameters are different requests however similar their wording,
so parameters go in the key and similarity is only ever asked to resolve
phrasing within one namespace (docs/TDD.md §10.1).

The classifier version prefixes the key so a classifier deploy writes into a
fresh namespace instead of serving entries under semantics that no longer hold.
"""
from typing import Mapping, Optional, Sequence

#: Parameters that do not change the answer and must not fragment the namespace.
#: `unit` is excluded deliberately: it changes only the rendering, and the tool
#: is asked for the right unit on the way in.
IGNORED_PARAMS = frozenset({"unit"})


def build_key(
    classifier_version: str,
    intent: str,
    params: Mapping[str, str],
    significant: Optional[Sequence[str]] = None,
) -> str:
    """`v3|current_weather|city=delhi|day=today`

    `significant` is the intent's declared parameter list (`IntentSpec.params`).
    Only those enter the key. Anything else the classifier happened to extract is
    incidental to *this* intent and must not fragment its namespace.

    Without that filter, "should I carry an umbrella today" and "should I carry
    an umbrella" produced two keys for one question: `clothing_advice` does not
    take a `day`, but the word "today" was extracted anyway and went into the
    key. Two entries, never a hit between them.

    Sorted, so parameter ordering cannot produce two keys for one request.
    """
    allowed = set(significant) if significant is not None else set(params)
    parts = [
        f"{k}={str(v).strip().lower()}"
        for k, v in sorted(params.items())
        if k in allowed and k not in IGNORED_PARAMS and str(v).strip()
    ]
    return "|".join([classifier_version, intent, *parts])
