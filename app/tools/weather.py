"""Open-Meteo.

Chosen because it needs no key and no signup, so the repo runs for a reviewer
immediately, and because it is fast enough not to distort latency measurements.

Two calls per lookup — geocode, then forecast — but geocoding is memoised: a
city's coordinates do not change, so the second time a city is asked about only
the forecast call remains.
"""
import asyncio
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import httpx

from app import retry
from app.config import settings
from app.tools.base import ToolResult, ToolSpec

log = logging.getLogger("sarjy.weather")

SPEC = ToolSpec(name="weather", freshness_seconds=600, timeout_s=4.0)


class Unreachable(RuntimeError):
    """The upstream could not be reached. Distinct from "no such city", because
    they warrant different things being said out loud."""

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "fixtures" / "weather.json"

# WMO weather codes -> words a person would say out loud.
WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "a thunderstorm", 96: "a thunderstorm with hail",
    99: "a severe thunderstorm with hail",
}

# Open-Meteo geocodes by current official name only, so a common colloquial name
# returns nothing or the wrong place: "Bangalore" resolves to Bangalore Town,
# Pakistan (pop. ~10k) because the Indian city is indexed as Bengaluru. Speech
# input is whatever the user actually said, so the alias is resolved on our side.
ALIASES = {
    "bangalore": "Bengaluru", "blr": "Bengaluru",
    "bombay": "Mumbai", "calcutta": "Kolkata", "madras": "Chennai",
    "poona": "Pune", "baroda": "Vadodara", "trivandrum": "Thiruvananthapuram",
    "gurgaon": "Gurugram", "mysore": "Mysuru",
    "peking": "Beijing", "saigon": "Ho Chi Minh City", "rangoon": "Yangon",
    "nyc": "New York", "sf": "San Francisco", "la": "Los Angeles",
    "delhi": "New Delhi",
}

# Coordinates are immutable, so this never needs invalidating. Bounded so a
# long-lived process cannot grow without limit.
_GEO_CACHE: "OrderedDict[str, Optional[dict]]" = OrderedDict()
_GEO_CACHE_MAX = 512

_client: Optional[httpx.AsyncClient] = None
_fixtures: Optional[dict] = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            # Open-Meteo answers in ~150ms. A slow connect means a dead one, and
            # a person is waiting for a spoken answer, so fail fast and retry
            # rather than sitting on a socket.
            timeout=httpx.Timeout(connect=2.0, read=SPEC.timeout_s, write=2.0, pool=2.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def canonical(city: str) -> str:
    key = (city or "").strip().lower()
    return ALIASES.get(key, city)


def _best_match(query: str, results: list) -> dict:
    """Open-Meteo returns matches unranked by size, so `count=1` can hand back a
    village that happens to share a name. Prefer an exact name match, then
    population."""
    wanted = query.strip().lower()

    def score(r: dict):
        return ((r.get("name") or "").strip().lower() == wanted,
                r.get("population") or 0)

    return max(results, key=score)


# ---------------------------------------------------------------- fixtures --
def _load_fixtures() -> dict:
    global _fixtures
    if _fixtures is None:
        try:
            _fixtures = json.loads(FIXTURE_PATH.read_text())
        except (OSError, ValueError):
            _fixtures = {}
    return _fixtures


def _record(key: str, payload: dict) -> None:
    """Evaluation runs replay recorded upstream responses so that a baseline and
    a treatment run are not separated by the weather actually changing
    (docs/TDD.md §12)."""
    data = _load_fixtures()
    data[key] = payload
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(data, indent=1, sort_keys=True))


# ---------------------------------------------------------------- geocoding --
async def geocode(city: str) -> Optional[dict]:
    name = canonical(city)
    key = name.strip().lower()
    if key in _GEO_CACHE:
        _GEO_CACHE.move_to_end(key)
        return _GEO_CACHE[key]

    async def _get() -> httpx.Response:
        return await client().get(
            GEOCODE_URL,
            params={"name": name, "count": 10, "language": "en", "format": "json"},
        )

    try:
        resp = await retry.call(
            _get, label="open-meteo:geocode", attempts=2, deadline_s=5.0)
        results = resp.json().get("results") or []
    except (retry.RetryExhausted, httpx.HTTPError, ValueError) as exc:
        log.warning("geocode failed for %r: %s", city, exc)
        # Raised, not returned as None. None means "no such city", which the
        # caller turns into "I couldn't find that place" -- the wrong thing to
        # say when the real cause was a network failure. Also not cached: one
        # bad minute would otherwise persist for the process lifetime.
        raise Unreachable(str(exc)) from exc

    loc = _best_match(name, results) if results else None
    # Negative results are cached: a misheard city would otherwise cost a full
    # round trip every time the user repeats themselves.
    _GEO_CACHE[key] = loc
    if len(_GEO_CACHE) > _GEO_CACHE_MAX:
        _GEO_CACHE.popitem(last=False)
    return loc


# ----------------------------------------------------------------- fetching --
def _describe(loc: dict, cur: dict, daily: dict, day: str, unit: str) -> str:
    place = ", ".join(x for x in (loc.get("name"), loc.get("country")) if x)
    u = "F" if unit == "fahrenheit" else "C"
    desc = WMO.get(cur.get("weather_code"), "unclear conditions")

    idx = 1 if day == "tomorrow" and len(daily.get("time") or []) > 1 else 0
    parts = [f"Location: {place}."]

    if day != "tomorrow":
        parts.append(
            f"Right now: {desc}, {cur.get('temperature_2m')} degrees {u}, "
            f"feels like {cur.get('apparent_temperature')}, "
            f"humidity {cur.get('relative_humidity_2m')} percent, "
            f"wind {cur.get('wind_speed_10m')} km/h."
        )

    highs = (daily.get("temperature_2m_max") or [None] * 2)
    lows = (daily.get("temperature_2m_min") or [None] * 2)
    rain = (daily.get("precipitation_probability_max") or [None] * 2)
    codes = (daily.get("weather_code") or [None] * 2)
    label = "Tomorrow" if idx == 1 else "Today"
    if highs[idx] is not None:
        parts.append(
            f"{label}: {WMO.get(codes[idx], 'unclear')}, "
            f"high {highs[idx]}, low {lows[idx]} degrees {u}, "
            f"chance of precipitation {rain[idx]} percent."
        )

    sr = (daily.get("sunrise") or [None])[0]
    ss = (daily.get("sunset") or [None])[0]
    if sr and ss:
        parts.append(f"Sunrise {sr.split('T')[-1]}, sunset {ss.split('T')[-1]} local time.")

    return " ".join(parts)


async def fetch(city: str, day: str = "today", unit: str = "celsius") -> ToolResult:
    """Returns plain facts for the prompt, or ok=False. Never returns a guess —
    a failure here must produce 'I don't know', not an invented temperature."""
    if not city:
        return ToolResult(ok=False, error="no_city")

    cache_key = f"{canonical(city).lower()}|{day}|{unit}"

    if settings.tool_source == "fixture":
        hit = _load_fixtures().get(cache_key)
        if hit is None:
            return ToolResult(ok=False, error="fixture_miss")
        return ToolResult(ok=True, context=hit["context"],
                          freshness_seconds=SPEC.freshness_seconds,
                          meta={"source": "fixture", **hit.get("meta", {})})

    try:
        loc = await geocode(city)
    except Unreachable as exc:
        return ToolResult(ok=False, error="upstream", meta={"detail": str(exc)[:120]})
    if not loc:
        return ToolResult(ok=False, error="unknown_city")

    temp_unit = "fahrenheit" if unit == "fahrenheit" else "celsius"
    async def _forecast() -> httpx.Response:
        return await client().get(
            FORECAST_URL,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                           "wind_speed_10m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                         "precipitation_probability_max,sunrise,sunset",
                "forecast_days": 2,
                "timezone": "auto",
                "temperature_unit": temp_unit,
            },
        )

    try:
        resp = await retry.call(
            _forecast, label="open-meteo:forecast", attempts=2, deadline_s=5.0)
        body = resp.json()
    except (retry.RetryExhausted, httpx.HTTPError, ValueError) as exc:
        log.warning("forecast failed for %r: %s", city, exc)
        return ToolResult(ok=False, error="upstream")

    cur = body.get("current") or {}
    daily = body.get("daily") or {}
    if not cur and not daily:
        return ToolResult(ok=False, error="empty_response")

    context = _describe(loc, cur, daily, day, unit)
    meta = {"source": "live", "resolved": loc.get("name"), "country": loc.get("country")}

    if settings.record_fixtures:
        _record(cache_key, {"context": context, "meta": meta})

    return ToolResult(ok=True, context=context,
                      freshness_seconds=SPEC.freshness_seconds, meta=meta)


async def status() -> str:
    return f"ok ({settings.tool_source})"
