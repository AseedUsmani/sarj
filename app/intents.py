"""Intent registry.

The classifier decides *which label* applies to a request. What the system does
with that label — cache it or not, which tier, how long it stays fresh — is
backend configuration and lives here.

Keeping these separate is what lets the classifier be swapped for a learned one
without touching routing or cache policy.
"""
from dataclasses import dataclass
from typing import Literal

Tier = Literal["small", "large"]
TTLGroup = Literal["current", "forecast", "static", "none"]

# Freshness is owned by the data, not the cache. Current conditions go stale
# fast; a greeting never does.
TTL_SECONDS: dict[str, int] = {
    "current": 10 * 60,
    "forecast": 60 * 60,
    "static": 24 * 60 * 60,
    "none": 0,
}


@dataclass(frozen=True)
class IntentSpec:
    name: str
    params: tuple[str, ...]      # required before a cache key can be built
    cacheable: bool
    ttl_group: TTLGroup
    tier: Tier
    needs_tool: bool


def _s(name, params=(), cacheable=True, ttl="current", tier="small", tool=True):
    return IntentSpec(name, params, cacheable, ttl, tier, tool)


REGISTRY: dict[str, IntentSpec] = {s.name: s for s in [
    # --- lookups: the model formats data the tool returned -----------------
    _s("current_weather",   ("city",)),
    _s("temperature",       ("city",)),
    _s("rain_now",          ("city",)),
    _s("wind",              ("city",)),
    _s("humidity",          ("city",)),
    _s("sunrise_sunset",    ("city",)),

    # --- forecast ----------------------------------------------------------
    _s("forecast_today",    ("city",),        ttl="forecast"),
    _s("forecast_tomorrow", ("city",),        ttl="forecast"),
    _s("rain_forecast",     ("city", "day"),  ttl="forecast"),
    _s("temp_range",        ("city", "day"),  ttl="forecast"),

    _s("compare_cities",    ("city", "city_b")),

    # --- advice: uncacheable. The tool result changes underneath an
    #     identical question, so a stored answer goes quietly out of date.
    _s("clothing_advice",   ("city",),          cacheable=False, ttl="none", tier="large"),
    _s("travel_advice",     ("city", "day"),    cacheable=False, ttl="none", tier="large"),
    _s("activity_advice",   ("city",),          cacheable=False, ttl="none", tier="large"),

    # --- memory: writes or reads stored facts, no tool ----------------------
    _s("set_home_city",     ("city",),  cacheable=False, ttl="none", tool=False),
    _s("set_units",         ("unit",),  cacheable=False, ttl="none", tool=False),
    _s("recall_fact",       (),         cacheable=False, ttl="none", tool=False),

    # --- resolves to current_weather with the stored city ------------------
    _s("weather_at_home",   ()),

    # --- conversational: no tool, cacheable because the answer is fixed -----
    _s("greeting",   (), ttl="static", tool=False),
    _s("thanks",     (), ttl="static", tool=False),
    _s("out_of_scope", (), ttl="static", tool=False),

    # --- cannot be answered from this request alone ------------------------
    _s("follow_up", (), cacheable=False, ttl="none", tier="large", tool=False),
    _s("unknown",   (), cacheable=False, ttl="none", tier="large", tool=False),
]}


def spec(name: str) -> IntentSpec:
    return REGISTRY.get(name, REGISTRY["unknown"])


def ttl_seconds(name: str) -> int:
    return TTL_SECONDS[spec(name).ttl_group]
