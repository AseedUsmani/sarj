"""Typed settings from environment variables. No config files, no secrets in
the repo. See docs/TDD.md §13."""
import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

# Load .env before any os.getenv default is evaluated below. Real environment
# variables always win, so Render's dashboard values are not overridden by a
# stray .env in the image.
load_dotenv(override=False)

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
    # SQLite only. See app/db.py for why, and for the ephemeral-filesystem caveat.
    sqlite_path: str = os.getenv("SQLITE_PATH", "./sarjy.db")

    # Cache and classifier knobs. Thresholds are configuration, not constants,
    # so the sweep's output applies without a code change (TDD §13).
    # Set from the measured paraphrase floor (docs/FINDINGS.md), not chosen a
    # priori. The key namespace does the safety work, so this only decides
    # whether two phrasings are the same question -- it can be permissive.
    # The earlier 0.92 was an embedding-cosine assumption and gave a 0% hit rate.
    similarity_threshold: float = _float("SIMILARITY_THRESHOLD", 0.20)
    confidence_floor: float = _float("CONFIDENCE_FLOOR", 0.6)
    classifier_version: str = os.getenv("CLASSIFIER_VERSION", "v1")

    # fixture | live — evaluation runs pin the upstream so only our code varies
    # (TDD §12).
    tool_source: str = os.getenv("TOOL_SOURCE", "live")

    # Model ids are configuration so a provider rename is not a code change --
    # which mattered immediately: the llama-3.x ids in the original design are
    # not served on this account. Verified available 2026-09-03.
    # TODO(pricing): confirm current per-token rates before quoting any cost
    # figure (docs/PRD.md open questions).
    small_model: str = os.getenv("SMALL_MODEL", "openai/gpt-oss-20b")
    large_model: str = os.getenv("LARGE_MODEL", "openai/gpt-oss-120b")
    upstream_timeout_s: float = _float("UPSTREAM_TIMEOUT_S", 20.0)
    # Must comfortably exceed reasoning + answer, or answers truncate mid-word.
    max_output_tokens: int = _int("MAX_OUTPUT_TOKENS", 400)
    # low | medium | high — see docs/FINDINGS.md
    reasoning_effort: str = os.getenv("REASONING_EFFORT", "low")
    max_text_chars: int = _int("MAX_TEXT_CHARS", 500)

    # Baseline evaluation runs record upstream responses so later runs replay
    # them; otherwise a baseline/treatment comparison also measures the weather
    # changing between runs (docs/TDD.md §12).
    record_fixtures: bool = os.getenv("RECORD_FIXTURES", "").lower() in ("1", "true", "yes")

    # Signs auth tokens. Unset means a random per-boot value, which invalidates
    # every token on restart -- fine locally, wrong in deployment, and warned
    # about at startup.
    auth_secret: str = os.getenv("AUTH_SECRET", "")

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

if not settings.auth_secret:
    import secrets as _secrets
    object.__setattr__(settings, "auth_secret", _secrets.token_urlsafe(32))
