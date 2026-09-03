"""Rule-based classifier — the reference stand-in.

Deliberately simple and deterministic. It exists so the service is runnable and
testable end to end; it is a placeholder, not a design position. A learned
classifier drops in behind the same contract (see base.py).

Order matters: the most specific patterns are tested first, because "should I
take an umbrella in Delhi" contains both an advice cue and a rain cue.
"""
import re
from typing import Optional

from app.classifier.base import Classification

VERSION = "rules-v1"

# Words that follow "in/for/at" but are never places.
_NOT_A_PLACE = {
    "the", "a", "an", "here", "there", "it", "that", "this", "me", "my", "you",
    "us", "today", "tomorrow", "tonight", "now", "morning", "evening", "night",
    "celsius", "fahrenheit", "degrees", "general", "short", "summary", "home",
}

# "in Delhi", "for New York", "at Mumbai" — stops at punctuation, a time word,
# or a conjunction so "in Paris tomorrow" does not capture "tomorrow".
_PLACE = re.compile(
    r"\b(?:in|at|for|around|near|of|to)\s+"
    r"([A-Za-z][A-Za-z\s.'-]{1,38}?)"
    r"(?=\s*(?:[?,.!]|$|\b(?:today|tomorrow|tonight|now|right|this|next|and|or|"
    r"please|like|going|gonna|weather|forecast|temperature)\b))",
    re.I,
)
_COMPARE = re.compile(r"\b(?:compare|versus|vs\.?|or)\b", re.I)

_RULES: list[tuple[str, re.Pattern]] = [
    # Follow-ups first. They are meaningful only against the previous turn, so
    # misclassifying one as a cacheable lookup is a correctness bug, not a
    # quality one: "what about tomorrow?" would be stored and served to someone
    # whose previous turn was about a different city.
    ("follow_up", re.compile(
        r"^\s*(?:what about|how about|and what about|what if|same for|and there)\b"
        r"|^\s*and\s+(?:in|for|what)\b", re.I)),

    # comparison before plain lookups — it mentions weather words too
    ("compare_cities", re.compile(
        r"\b(?:compare|versus|\bvs\.?\b)\b", re.I)),

    # memory — these are commands, not questions
    ("set_home_city", re.compile(
        r"\b(?:my home (?:city|town) is|i live in|i am (?:in|from)|i'm (?:in|from)|"
        r"set (?:my )?home (?:city|town) (?:to|as)|remember (?:that )?i live in)\b", re.I)),
    ("set_units", re.compile(
        r"\b(?:use|switch to|in|prefer)\s+(celsius|fahrenheit|centigrade)\b", re.I)),
    ("recall_fact", re.compile(
        r"\b(?:what(?:'s| is) my|where do i live|do you remember|what did i tell you)\b", re.I)),

    # advice — checked before plain lookups, since they mention conditions too
    ("clothing_advice", re.compile(
        r"\b(?:carry|take|bring|need|want|wear|pack)\b[^?.!]{0,40}?"
        r"\b(?:umbrella|raincoat|rain coat|jacket|coat|sweater|jumper|hoodie|"
        r"shorts|sunscreen|sunglasses|scarf|gloves|boots|clothes|clothing|"
        r"woollens|woolens|warm clothes|winter clothes)\b"
        r"|\bwhat should i wear\b|\bdress for\b|\b(?:warm|cold) enough\b",
        re.I)),
    ("travel_advice", re.compile(
        r"\b(?:should i (?:travel|drive|fly|go|visit)"
        r"|(?:travel|travell)ing to|trip to|heading to|driving to|flying to"
        r"|good (?:day|time) to (?:travel|drive|visit)"
        r"|safe to (?:drive|travel))\b", re.I)),
    ("activity_advice", re.compile(
        r"\b(?:good (?:day|weather) (?:for|to)|should i (?:run|walk|cycle|picnic)|"
        r"can i (?:go|play)|plan a)\b", re.I)),

    # forecast
    ("forecast_tomorrow", re.compile(r"\btomorrow\b", re.I)),
    ("rain_forecast", re.compile(
        r"\b(?:will it|is it going to|gonna)\s+(?:rain|snow|pour)\b", re.I)),
    ("temp_range", re.compile(r"\b(?:high and low|max and min|temperature range)\b", re.I)),
    ("forecast_today", re.compile(r"\b(?:forecast|rest of (?:the )?day|later today)\b", re.I)),

    # current conditions
    ("sunrise_sunset", re.compile(r"\b(?:sunrise|sunset|sun (?:rise|set))\b", re.I)),
    ("humidity", re.compile(r"\bhumid(?:ity)?\b", re.I)),
    ("wind", re.compile(r"\bwind(?:y|s)?\b", re.I)),
    ("rain_now", re.compile(r"\b(?:raining|snowing|pouring|drizzl)", re.I)),
    ("temperature", re.compile(
        r"\b(?:temperature|how (?:hot|cold|warm)|degrees|how many degrees)\b", re.I)),
    ("current_weather", re.compile(
        r"\b(?:weather|conditions"
        r"|how(?:'s| is)\s+(?:it\s+)?(?:[A-Za-z][A-Za-z\s'-]{1,30}\s+)?"
        r"(?:outside|looking|doing|out there))\b", re.I)),

    # conversational
    ("greeting", re.compile(r"^\s*(?:hi|hey|hello|good (?:morning|evening|afternoon))\b", re.I)),
    ("thanks", re.compile(r"\b(?:thanks|thank you|cheers|appreciate it)\b", re.I)),

]

_UNIT = re.compile(r"\b(celsius|centigrade|fahrenheit)\b", re.I)
_DAY = re.compile(r"\b(today|tomorrow|tonight)\b", re.I)

# Horizons the tool cannot serve. Detected so the assistant can say so, rather
# than silently answering for today -- confidently wrong advice about a trip
# next week is worse than admitting the limit.
_BEYOND = re.compile(
    r"\b(?:next\s+(?:week|month|year)|this\s+weekend|in\s+\d+\s+(?:days|weeks)"
    r"|day after tomorrow|later this week|end of (?:the )?(?:week|month))\b", re.I)


# Tokens that can never be part of a place name. A place name is what is left.
_NON_PLACE = {
    "should", "shall", "would", "could", "can", "will", "may", "might", "must",
    "do", "does", "did", "am", "i", "we", "they", "he", "she", "you", "it",
    "take", "bring", "carry", "need", "want", "wear", "pack", "check", "tell",
    "moving", "move", "leaving", "leave", "planning", "plan", "think",
    "ok", "okay", "so", "just", "also", "please", "thanks", "hey", "and", "or",
    "but", "if", "when", "while", "there", "then", "an", "a", "the", "my", "me",
    # question words and contractions: "what's the weather" must not yield
    # "what's" as a place when the place leads the sentence elsewhere
    "what", "whats", "what's", "how", "hows", "how's", "where", "wheres",
    "where's", "why", "who", "which", "tell", "give", "show", "s",
    # time references: "to Delhi early next week" must not yield "delhi early"
    "early", "late", "later", "next", "last", "this", "week", "weekend",
    "month", "year", "soon", "day", "days", "morning", "afternoon", "evening",
    "night", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
    # garments: "carry winter clothes" must not yield "winter clothes"
    "winter", "summer", "warm", "cold", "clothes", "clothing", "woollens",
    "woolens", "jacket", "coat", "sweater", "umbrella", "raincoat", "shorts",
    "sunscreen", "boots", "scarf", "gloves",
}


def _is_place_token(tok: str) -> bool:
    return (
        tok not in _NOT_A_PLACE
        and tok not in _PREPS
        and tok not in _NON_PLACE
        and not _WEATHER_ISH.fullmatch(tok)
        and not _VERBISH.fullmatch(tok)
    )


def _clean_place(raw: str) -> Optional[str]:
    place = " ".join(raw.split()).strip(" .,'-").lower()
    if not place or place in _NOT_A_PLACE:
        return None
    # The 'to' preposition over-captures, and in both directions:
    #   "to be sunny in goa"               junk at the head, place at the tail
    #   "to dubai should i take umbrella"  place at the head, junk at the tail
    # A trim from either end gets one case and destroys the other. Split into
    # runs of consecutive place-like tokens and keep the FIRST — the capture
    # starts immediately after the preposition, so the place leads:
    #   "to be sunny in goa"        -> [] [goa]            -> goa
    #   "to dubai should i take"    -> [dubai] []          -> dubai
    #   "to delhi early next week"  -> [delhi] [...]       -> delhi
    # Taking the longest run instead picks "winter clothes" over "delhi".
    tokens = place.split()
    runs, current = [], []
    for tok in tokens:
        if _is_place_token(tok):
            current.append(tok)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not runs:
        return None
    place = " ".join(runs[0])
    # Strip a trailing stopword: "delhi please" -> "delhi"
    parts = [p for p in place.split() if p not in _NOT_A_PLACE]
    return " ".join(parts) or None


# Vocabulary that means "this is about the weather" even when no rule matched.
_WEATHER_ISH = re.compile(
    r"\b(?:weather|temperature|forecast|rain|raining|rainy|snow|snowing|sunny|"
    r"sun|cloudy|overcast|storm|humid|humidity|wind|windy|hot|cold|warm|chilly|"
    r"freezing|muggy|umbrella|raincoat|jacket|coat|sweater|shorts|sunscreen|"
    r"degrees|climate|drizzle|monsoon)\b", re.I)

_PREPS = {"in", "at", "for", "to", "of", "near", "around", "with", "while",
          "from", "on", "by"}

# Verbs and fillers that can only mean the capture ran past the place name.
_VERBISH = re.compile(
    r"\b(?:be|been|being|go|going|get|getting|stay|staying|look|looking|"
    r"visit|visiting|travel|travelling|traveling|come|coming|is|are|was)\b",
    re.I)

# "should I ...", "do I need ...", "is it a good idea to ..."
_ASKING_ADVICE = re.compile(
    r"\b(?:should i|do i need|shall i|is it (?:a )?good|worth|can i|ought i)\b",
    re.I)

_SET_HOME = re.compile(
    r"\b(?:home (?:city|town)\s+(?:to|as|is)|i live in|i'm in|i am in|i'm from|i am from)\s+"
    r"([A-Za-z][A-Za-z\s.'-]{1,38}?)(?=\s*(?:[?,.!]|$))", re.I)

# "at home", "my home town" — resolves from stored facts, not a place name
_HOME = re.compile(r"\b(?:at|back|my) home\b|\bhome (?:city|town)\b", re.I)
# "delhi weather", "mumbai forecast" — the place leads and no preposition
# appears, so _PLACE never fires.
_CITY_FIRST = re.compile(
    r"^\s*([A-Za-z][A-Za-z\s.'-]{1,30}?)\s+"
    r"(?:weather|forecast|temperature|conditions|climate)\b", re.I)
_HOWS_CITY = re.compile(
    r"\bhow(?:'s|s| is)\s+([A-Za-z][A-Za-z\s.'-]{1,30}?)\s+"
    r"(?:looking|doing|out there|today|now)", re.I)
_COMPARE_PAIR = re.compile(
    r"\b(?:compare|between)?\s*([A-Za-z][A-Za-z\s.'-]{1,30}?)\s+"
    r"(?:and|versus|vs\.?|or)\s+([A-Za-z][A-Za-z\s.'-]{1,30}?)(?=\s*(?:[?,.!]|$))", re.I)


def extract_params(text: str, intent: str) -> dict[str, str]:
    params: dict[str, str] = {}

    cities = [c for c in (_clean_place(m.group(1)) for m in _PLACE.finditer(text)) if c]

    if intent == "compare_cities":
        pair = _COMPARE_PAIR.search(text)
        if pair:
            a, b = _clean_place(pair.group(1)), _clean_place(pair.group(2))
            cities = [c for c in (a, b) if c] or cities
    elif intent == "set_home_city":
        m = _SET_HOME.search(text)
        if m:
            c = _clean_place(m.group(1))
            if c:
                cities = [c]
    elif not cities:
        for pattern in (_HOWS_CITY, _CITY_FIRST):
            m = pattern.search(text)
            if m:
                c = _clean_place(m.group(1))
                if c:
                    cities = [c]
                    break

    if cities:
        params["city"] = cities[0]
        if intent == "compare_cities" and len(cities) > 1:
            params["city_b"] = cities[1]

    unit = _UNIT.search(text)
    if unit:
        u = unit.group(1).lower()
        params["unit"] = "fahrenheit" if u == "fahrenheit" else "celsius"

    if _BEYOND.search(text):
        # Marked explicitly rather than silently falling back to today.
        params["day"] = "beyond_forecast"
        return params

    day = _DAY.search(text)
    if day:
        params["day"] = "today" if day.group(1).lower() == "tonight" else day.group(1).lower()
    elif intent in ("forecast_tomorrow",):
        params["day"] = "tomorrow"
    elif intent in ("rain_forecast", "temp_range", "travel_advice"):
        params["day"] = "today"

    return params


class RuleClassifier:
    """Deterministic by construction: the same text always yields the same
    intent, which is the contract term the cache key depends on."""

    version = VERSION

    def classify(self, text: str) -> Classification:
        t = (text or "").strip()
        if not t:
            return Classification("unknown", {}, 0.0, self.version)

        intent = "unknown"
        confidence = 0.3
        for name, pattern in _RULES:
            if pattern.search(t):
                intent = name
                confidence = 0.9
                break

        # Two cities plus a comparison cue outranks a plain lookup.
        if intent in ("current_weather", "temperature") and _COMPARE.search(t):
            if len(list(_PLACE.finditer(t))) > 1:
                intent = "compare_cities"

        params = extract_params(t, intent)

        # "weather at home" is a lookup whose city comes from stored facts.
        if intent in ("current_weather", "temperature") and "city" not in params \
                and _HOME.search(t):
            intent = "weather_at_home"

        # A bare place name is a weather question. Sarjy asks "where do you
        # live? ... or just name a city", so the reply is often one or two
        # words with no weather vocabulary at all. Confidence stays low: if it
        # is not really a place the geocoder says so, and low confidence keeps
        # it out of the cache either way.
        if intent == "unknown":
            toks = [w for w in re.findall(r"[A-Za-z']+", t)]
            if 1 <= len(toks) <= 3 and all(_is_place_token(w.lower()) for w in toks):
                return Classification(
                    "current_weather", {"city": " ".join(w.lower() for w in toks)},
                    0.5, self.version)

        # Last-resort fallback. A question that is clearly weather-adjacent must
        # still reach the tool: answering "shall I look it up?" when we could
        # simply look it up is the worst of both.
        if intent == "unknown" and _WEATHER_ISH.search(t):
            if _ASKING_ADVICE.search(t):
                # Advice needs conditions, so it is tool-backed and goes large.
                intent, confidence = "clothing_advice", 0.6
            else:
                intent, confidence = "current_weather", 0.5

        return Classification(intent, params, confidence, self.version)
