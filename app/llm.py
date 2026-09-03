"""Groq client.

The upstream is a metered third-party dependency, not something this service
reasons about. Its job here is: send messages, return text and a token count,
and fail in a way the pipeline can handle.

Token counts come from the provider's usage block rather than being estimated,
because every cost figure downstream is computed from them.
"""
import logging
from dataclasses import dataclass
from typing import Literal, Optional

import httpx

from app import retry
from app.config import settings

log = logging.getLogger("sarjy.llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

Tier = Literal["small", "large"]


class UpstreamError(RuntimeError):
    """Upstream unreachable, refused, or malformed. Never surfaced as an answer."""


@dataclass(frozen=True)
class Completion:
    text: str
    tier: Tier
    model: str
    tokens_in: int
    tokens_out: int
    #: gpt-oss models emit hidden reasoning that bills as output tokens.
    reasoning_tokens: int = 0
    truncated: bool = False


_client: Optional[httpx.AsyncClient] = None


#: Connection setup is normally tens of ms; a slow one is a dead connection,
#: not a slow model, so it is worth failing fast and retrying.
CONNECT_TIMEOUT_S = 10.0


def client() -> httpx.AsyncClient:
    """One shared client so connections stay warm — a fresh TLS handshake per
    request is pure added latency on the hot path."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_S,
                read=settings.upstream_timeout_s,
                write=10.0,
                pool=10.0,
            ),
            limits=httpx.Limits(max_keepalive_connections=16, max_connections=32),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def model_for(tier: Tier) -> str:
    return settings.small_model if tier == "small" else settings.large_model


async def complete(
    messages: list[dict],
    tier: Tier,
    max_tokens: Optional[int] = None,
    temperature: float = 0.3,
) -> Completion:
    if not settings.groq_api_key:
        raise UpstreamError("GROQ_API_KEY is not configured")

    model = model_for(tier)
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or settings.max_output_tokens,
        "temperature": temperature,
        # gpt-oss are reasoning models: by default ~250 of ~330 completion
        # tokens go to hidden reasoning, which bills as output and starves the
        # answer of budget. 'low' cuts output ~4x with no quality loss for
        # single-sentence weather answers. See docs/FINDINGS.md.
        "reasoning_effort": settings.reasoning_effort,
    }
    async def _post() -> httpx.Response:
        return await client().post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json=payload,
        )

    try:
        resp = await retry.call(_post, label=f"groq:{tier}")
    except retry.RetryExhausted as exc:
        raise UpstreamError(str(exc)) from exc
    except httpx.HTTPError as exc:
        # Answered by the server, or otherwise not transient: not retried.
        raise UpstreamError(f"{type(exc).__name__}: {exc}") from exc

    if resp.status_code == 429:
        raise UpstreamError("rate limited by provider")
    if resp.status_code >= 400:
        raise UpstreamError(f"status {resp.status_code}: {resp.text[:200]}")

    try:
        body = resp.json()
        choice = body["choices"][0]
        text = choice["message"].get("content") or ""
        finish = choice.get("finish_reason")
        usage = body.get("usage") or {}
    except (KeyError, IndexError, ValueError) as exc:
        raise UpstreamError(f"malformed response: {exc}") from exc

    reasoning = int((usage.get("completion_tokens_details") or {})
                    .get("reasoning_tokens") or 0)
    truncated = finish == "length"

    if truncated:
        # Silent truncation is the worst outcome: a half-sentence read aloud
        # sounds like a bug in the assistant rather than a budget problem.
        log.warning("truncated at max_tokens model=%s reasoning=%d", model, reasoning)
    if not text.strip():
        raise UpstreamError(
            f"empty content (finish={finish}, reasoning_tokens={reasoning})")

    return Completion(
        text=_normalise(text),
        tier=tier,
        model=model,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=reasoning,
        truncated=truncated,
    )


# The models emit narrow no-break spaces and similar; they render oddly and can
# confuse speech synthesis.
_ODD_SPACE = str.maketrans({"\u202f": " ", "\u00a0": " ", "\u2009": " ", "\u2011": "-"})


def _normalise(text: str) -> str:
    return " ".join(text.translate(_ODD_SPACE).split()).strip()


async def status() -> str:
    """For /health. Reports configuration, not reachability — probing the
    upstream on every health check would burn quota on the keep-warm ping."""
    return "configured" if settings.groq_api_key else "unconfigured"
