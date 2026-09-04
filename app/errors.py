"""User-facing error messages.

An error has two audiences and they need different text. The operator needs the
cause — "ConnectTimeout after 1003ms", "status 401: invalid_api_key". The person
talking to the assistant needs to know what happened to *them*, and hearing
"GROQ_API_KEY is not configured" read aloud is both useless and a disclosure of
how the service is put together.

So the code is the contract, the detail goes to the log with the trace id, and
this maps the code to something worth saying out loud.
"""

#: code -> (http status, what the user hears)
USER_FACING: dict[str, tuple[int, str]] = {
    "upstream_timeout": (
        503,
        "I'm having trouble reaching my services right now. Please try again "
        "in a moment.",
    ),
    "rate_limited": (
        429,
        "I'm handling a lot of requests right now. Give me a few seconds and "
        "try again.",
    ),
    "tool_unavailable": (
        503,
        "I couldn't reach the weather service just now. Please try again in a "
        "moment.",
    ),
    "invalid_request": (400, "Sorry, I couldn't understand that request."),
    "internal": (500, "Something went wrong on my side. Please try again."),
}

_FALLBACK = (500, "Something went wrong on my side. Please try again.")


def status_for(code: str) -> int:
    return USER_FACING.get(code, _FALLBACK)[0]


def message_for(code: str) -> str:
    """Never returns the internal detail. If a code is unmapped, the generic
    message is used rather than leaking whatever string was raised."""
    return USER_FACING.get(code, _FALLBACK)[1]
