from app.classifier.base import Classification, Classifier
from app.classifier.rules import RuleClassifier

_active: Classifier = RuleClassifier()


def classify(text: str) -> Classification:
    return _active.classify(text)


def set_classifier(c: Classifier) -> None:
    """Swap the implementation. The service depends on the protocol, not on
    which implementation is installed."""
    global _active
    _active = c


def version() -> str:
    """The active implementation's version. Enters the cache key, so anything
    building a key outside the normal path needs it too."""
    return _active.version


def status() -> str:
    return f"ok ({_active.version})"
