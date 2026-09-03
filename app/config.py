"""Typed settings from environment variables. No config files, no secrets in
the repo. See docs/TDD.md §13."""
import os
from dataclasses import dataclass
from typing import Literal

Mode = Literal["baseline", "router", "full"]
VALID_MODES: tuple[str, ...] = ("baseline", "router", "full")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from None


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a float, got {raw!r}") from None


@dataclass(frozen=True)
class Settings:
    # Service
    port: int = _int("PORT", 8000)
    mode: str = os.getenv("MODE", "full")
    git_sha: str = os.getenv("GIT_SHA", "dev")

    # Upstreams — absent at step 1; readiness reports them as unconfigured.
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    database_url: str = os.getenv("DATABASE_URL", "")

    # Cache and classifier knobs. Thresholds are configuration, not constants,
    # so the sweep's output applies without a code change (TDD §13).
    similarity_threshold: float = _float("SIMILARITY_THRESHOLD", 0.92)
    confidence_floor: float = _float("CONFIDENCE_FLOOR", 0.6)
    classifier_version: str = os.getenv("CLASSIFIER_VERSION", "v1")

    # fixture | live — evaluation runs pin the upstream so only our code varies
    # (TDD §12).
    tool_source: str = os.getenv("TOOL_SOURCE", "live")

    rate_limit_per_min: int = _int("RATE_LIMIT_PER_MIN", 60)

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise RuntimeError(
                f"MODE must be one of {VALID_MODES}, got {self.mode!r}"
            )
        if self.tool_source not in ("live", "fixture"):
            raise RuntimeError(
                f"TOOL_SOURCE must be 'live' or 'fixture', got {self.tool_source!r}"
            )


settings = Settings()
