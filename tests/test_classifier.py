"""Classifier fixtures.

These are the phrasings the demo depends on, so they are regression-protected.
Two cases matter more than the rest:

- "How's Delhi looking?" must reach the same intent and params as "weather in
  Delhi", or the demo's cache hit does not happen.
- "What about tomorrow?" must be follow_up. Classifying it as a forecast would
  make it cacheable, and it would be served to someone whose previous turn was
  about a different city.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import classify

CASES = [
    ("What's the weather in Delhi?",         "current_weather",   "delhi"),
    ("How's Delhi looking?",                 "current_weather",   "delhi"),
    ("how is Mumbai doing?",                 "current_weather",   "mumbai"),
    ("weather in Mumbai",                    "current_weather",   "mumbai"),
    ("What's the temperature in New York?",  "temperature",       "new york"),
    ("Is it raining in London right now?",   "rain_now",          "london"),
    ("Will it rain in Bangalore tomorrow?",  "forecast_tomorrow", "bangalore"),
    ("Compare Delhi and Mumbai",             "compare_cities",    "delhi"),
    ("Delhi vs Chennai",                     "compare_cities",    "delhi"),
    ("I live in Bengaluru",                  "set_home_city",     "bengaluru"),
    ("set my home city to Pune",             "set_home_city",     "pune"),
    ("what's the weather at home?",          "weather_at_home",   None),
    ("What about tomorrow?",                 "follow_up",         None),
    ("what about Mumbai?",                   "follow_up",         None),
    ("Do I need a jacket in Bangalore?",     "clothing_advice",   "bangalore"),
    ("use fahrenheit",                       "set_units",         None),
    ("hello",                                "greeting",          None),
    ("who won the cricket match",            "unknown",           None),

    # Advice phrasings must reach the tool. Falling to `unknown` means no
    # weather is fetched and the assistant can only offer to look it up --
    # which is the worst answer available when we could simply look it up.
    ("Should I carry an umbrella with me while travelling to Mumbai?",
                                             "clothing_advice",   "mumbai"),
    ("do I need an umbrella in Mumbai",      "clothing_advice",   "mumbai"),
    ("should I carry a raincoat to Pune",    "clothing_advice",   "pune"),
    ("should I pack sunscreen for Dubai",    "clothing_advice",   "dubai"),
    ("should I wear shorts in Chennai",      "clothing_advice",   "chennai"),

    # The 'to' preposition can over-capture: "going to be sunny in Goa" must
    # yield "goa", not "be sunny in goa".
    ("is it going to be sunny in Goa",       "current_weather",   "goa"),
    ("is it going to rain in Kochi",         "rain_forecast",     "kochi"),
    ("will it be cold in Shimla",            "current_weather",   "shimla"),

    # Over-capture runs in both directions, so a trim from either end breaks
    # the other case. "to Dubai should I take an umbrella" puts the place at
    # the head; "to be sunny in Goa" puts it at the tail.
    ("ok I am moving to Dubai should I take an umbrella tomorrow",
                                             "clothing_advice",   "dubai"),
    ("I'm moving to Dubai, should I take an umbrella tomorrow?",
                                             "clothing_advice",   "dubai"),
    ("hey so what's the weather in Delhi",   "current_weather",   "delhi"),
    ("weather in san francisco",             "current_weather",   "san francisco"),

    # The place can lead, with no preposition to anchor on. Guard: question
    # words must not be captured as places ("what's the weather" -> not "what's").
    ("delhi weather please",                 "current_weather",   "delhi"),
    ("mumbai forecast",                      "forecast_today",    "mumbai"),
    ("bangalore temperature",                "temperature",       "bangalore"),

    # Common destinations are not places. "go to the beach" resolved to Beach,
    # North Dakota -- a real town, so geocoding succeeded and the answer was
    # confidently about the wrong continent. These must fall through to the
    # stored home city instead.
    ("is it a good day to go to the beach tomorrow",
                                             "activity_advice",   None),
    ("should I go to the park today",        "travel_advice",     None),
    ("is it good weather for the beach in Goa",
                                             "activity_advice",   "goa"),

    # Sarjy answers "where do you live? ... or just name a city", so a bare
    # place name must work. The prompt should not promise what the classifier
    # cannot do.
    ("Dubai",                                "current_weather",   "dubai"),
    ("new york",                             "current_weather",   "new york"),
]


def test_cases():
    failures = []
    for text, intent, city in CASES:
        r = classify(text)
        if r.intent != intent or r.params.get("city") != city:
            failures.append(f"{text!r}: got {r.intent}/{r.params.get('city')}, "
                            f"want {intent}/{city}")
    return failures


def test_determinism():
    """The cache key is built from classifier output, so identical input must
    always produce identical output (contract term, docs/TDD.md §4)."""
    failures = []
    for text, _, _ in CASES:
        a, b = classify(text), classify(text)
        if (a.intent, a.params, a.version if hasattr(a, "version") else a.model_version) != \
           (b.intent, b.params, b.model_version):
            failures.append(f"{text!r} not deterministic")
    return failures


def test_paraphrases_share_a_key():
    """The demo depends on these landing in the same namespace."""
    a, b = classify("What's the weather in Delhi?"), classify("How's Delhi looking?")
    if (a.intent, a.params.get("city")) != (b.intent, b.params.get("city")):
        return [f"paraphrase mismatch: {a.intent}/{a.params} vs {b.intent}/{b.params}"]
    return []


if __name__ == "__main__":
    all_failures = test_cases() + test_determinism() + test_paraphrases_share_a_key()
    for f in all_failures:
        print("FAIL", f)
    total = len(CASES) + 2
    print(f"\n{'PASS' if not all_failures else 'FAILED'}: "
          f"{total - len(all_failures)}/{total} checks")
    sys.exit(1 if all_failures else 0)


# ── spoken acknowledgements ──────────────────────────────────────────────────
# These only appear once the mic stays open. Nobody types "yeah"; everybody
# says it. Each one used to be read as a city name, and the geocoder confirmed
# it — "sure" is Suré in France, "okay" is Okay in Oklahoma, "guess" is
# Guessing in Austria — so the user was read real weather for a real village
# they had never named, with no error anywhere to notice.
ACK_CASES = [
    ("yes please",     "affirm"),
    ("yeah",           "affirm"),
    ("sure",           "affirm"),
    ("okay",           "affirm"),
    ("go ahead",       "affirm"),
    ("sounds good",    "affirm"),
    ("fine",           "affirm"),
    ("no",             "decline"),
    ("nope",           "decline"),
    ("no thanks",      "decline"),   # matches the thanks rule; is not gratitude
    ("never mind",     "decline"),
    ("maybe later",    "decline"),
    ("thanks!",        "thanks"),    # gratitude keeps its own reply
    ("thank you",      "thanks"),
    ("guess",          "unknown"),   # a common word, not Guessing, Austria
    ("hmm",            "unknown"),
    ("sorry",          "unknown"),
]


def test_acknowledgements_are_never_places():
    for text, expected in ACK_CASES:
        c = classify(text)
        assert c.intent == expected, f"{text!r} -> {c.intent} (want {expected})"
        assert "city" not in c.params, f"{text!r} yielded city={c.params.get('city')!r}"


# An acknowledgement in front of a real question is agreement plus content.
# Dropping the tail loses the entire question the user actually asked.
LEAD_CASES = [
    ("yes, what about delhi",       "follow_up",       None),
    ("yeah is it raining in goa",   "rain_now",        "goa"),
    ("sure, delhi",                 "current_weather", "delhi"),
    ("okay what's the weather in pune", "current_weather", "pune"),
]


def test_acknowledgement_prefix_keeps_the_question():
    for text, intent, city in LEAD_CASES:
        c = classify(text)
        assert c.intent == intent, f"{text!r} -> {c.intent} (want {intent})"
        assert c.params.get("city") == city, f"{text!r} -> {c.params}"


# Bare place names must still work: ASK_LOCATION promises "or just name a city".
def test_bare_place_names_still_resolve():
    for text in ("delhi", "new york", "san francisco"):
        c = classify(text)
        assert c.intent == "current_weather", f"{text!r} -> {c.intent}"
        assert c.params.get("city") == text


# Phrasings people speak rather than type when giving a location.
def test_spoken_location_phrasings():
    for text in ("my location is Dubai", "I'm based in Dubai", "I stay in Dubai"):
        c = classify(text)
        assert c.intent == "set_home_city", f"{text!r} -> {c.intent}"
        assert c.params.get("city") == "dubai", f"{text!r} -> {c.params}"
