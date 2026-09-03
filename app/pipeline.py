"""The request pipeline.

Orders the stages of a turn and owns the failure behaviour at each one.

    1 classify        -> intent, params, confidence, version   (external dep)
    2 gate            -> low confidence / unresolved -> uncacheable
    3 resolve         -> "home" -> stored city, units
    4 cache lookup    -> hit? return, no tool and no model call
    5 tool fetch      -> Open-Meteo, declares its own freshness
    7 route + call    -> small | large tier
    8 store           -> only on success; failures return before this
    7 log

Every failure path answers honestly. None of them invents weather data.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app import cache, classifier, intents, llm, memory, pending, request_log
from app.config import settings
from app.tools import weather

log = logging.getLogger("sarjy.pipeline")

# Asked whenever a weather question has no city and none is stored. Phrased as
# "where do you live" rather than "which city" because the answer is durable:
# it gets remembered, so the question is asked once rather than every time.
ASK_LOCATION = (
    "Where do you live? Tell me and I'll remember it, so you won't have to say "
    "it again. Or just name a city."
)

BASE_PROMPT = (
    "You are Sarjy, a friendly voice assistant for weather. Your reply is read "
    "aloud, so: one or two short sentences, plain words, no markdown, no lists, "
    "no emoji."
)

# Only applied when the turn is backed by tool data. Applying it to a greeting
# makes the model apologise for having no weather to report.
GROUNDING = (
    "\n\nUse only the data under 'Live data'. Never invent a temperature, "
    "forecast or place. If a detail is not in the data, do not state it."
)


@dataclass
class Answer:
    answer: str
    trace_id: str
    intent: Optional[str] = None
    params: dict = field(default_factory=dict)
    cached: bool = False
    route: Optional[str] = None
    confidence: Optional[float] = None
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "answer": self.answer, "intent": self.intent, "params": self.params,
            "cached": self.cached, "route": self.route,
            "confidence": self.confidence, "latency_ms": self.latency_ms,
            "trace_id": self.trace_id,
        }


class PipelineError(RuntimeError):
    def __init__(self, code: str, message: str, trace_id: str = "", status: int = 503):
        super().__init__(message)
        self.code = code
        self.trace_id = trace_id
        self.status = status


async def _resume(session_id: str, held, city: str, facts: dict,
                  mode: str, row, trace_id: str) -> Optional[str]:
    """Answer a question that was deferred while we asked for a location.

    Runs the tool-and-generate part of a turn with the city now known. Returns
    the answer text, or None if it cannot be produced — in which case the caller
    falls back to the plain acknowledgement, because a half-answer is worse than
    none.

    Deliberately not recursive: re-entering `handle` would re-classify, re-check
    memory intents and could defer again, and a loop is a worse failure than a
    missing convenience.
    """
    spec = intents.spec(held.intent)
    if not spec.needs_tool:
        return None

    params = dict(held.params)
    params["city"] = city
    unit = params.get("unit") or memory.unit(facts)

    try:
        result = await weather.fetch(
            city, day=params.get("day", "today"), unit=unit)
        row.tool_called = True
        if not result.usable:
            return None

        system = BASE_PROMPT + GROUNDING + "\n\nLive data:\n" + result.context
        if facts.get("unit"):
            system += f"\n\nThe user prefers {facts['unit']}."

        tier = "large" if mode == "baseline" else spec.tier
        completion = await llm.complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": held.text}],
            tier=tier,
        )
        row.route = tier
        row.tokens_in, row.tokens_out = completion.tokens_in, completion.tokens_out
        row.intent = held.intent
        return completion.text
    except llm.UpstreamError as exc:
        log.warning("resume failed trace=%s: %s", trace_id, exc)
        return None


async def handle(session_id: str, user_text: str, mode: str) -> Answer:
    started = time.perf_counter()
    trace_id = uuid.uuid4().hex[:12]
    row = request_log.LogRow(trace_id=trace_id, session_id=session_id,
                             mode=mode, latency_ms=0)

    async def finish(answer: str, route: Optional[str] = None,
                     cached: bool = False, cls=None) -> Answer:
        # Row is written here, after route and latency are known. Writing it
        # earlier persisted nulls for exactly the columns the evaluation needs.
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        row.route = route
        row.cached = cached
        await request_log.write(row)
        return Answer(
            answer=answer, trace_id=trace_id, route=route, cached=cached,
            latency_ms=row.latency_ms,
            intent=cls.intent if cls else None,
            params=dict(cls.params) if cls else {},
            confidence=cls.confidence if cls else None,
        )

    try:
        # --- 1. classify (external dependency) ---------------------------
        cls = classifier.classify(user_text)
        spec = intents.spec(cls.intent)
        row.intent, row.confidence = cls.intent, cls.confidence

        # --- 2. gate -----------------------------------------------------
        # Below the floor we do not trust the label enough to key a cache on it,
        # or to send it to the cheap tier. We do still trust it enough to fetch
        # the data: swapping in the `unknown` spec would drop needs_tool, and
        # the assistant would answer "shall I look it up?" for a question it
        # could simply have looked up. Degrade cacheability and tier, not
        # capability.
        trusted = cls.confidence >= settings.confidence_floor
        degrade_tier = not trusted

        facts = await memory.load(session_id)

        # --- 3. memory intents answer without an upstream call -----------
        if trusted and cls.intent == "set_home_city" and cls.params.get("city"):
            city = cls.params["city"]
            await memory.put(session_id, "home_city", city)
            ack = f"Got it, I'll remember {city.title()}."

            # If this reply answers a question we deferred, answer that too
            # rather than making the user ask again.
            held = pending.take(session_id)
            if held:
                facts["home_city"] = city
                resumed = await _resume(
                    session_id, held, city, facts, mode, row, trace_id)
                if resumed:
                    return await finish(f"{ack} {resumed}", route=row.route, cls=cls)
            return await finish(ack, cls=cls)

        if trusted and cls.intent == "set_units" and cls.params.get("unit"):
            u = cls.params["unit"]
            await memory.put(session_id, "unit", u)
            return await finish(f"Okay, I'll use {u} from now on.", cls=cls)

        if trusted and cls.intent == "recall_fact":
            return await finish(memory.describe(facts), cls=cls)

        # --- 4. resolve --------------------------------------------------
        params = dict(cls.params)
        unit = params.get("unit") or memory.unit(facts)

        # "weather at home", or any weather question with no city, resolves to
        # the stored city. This is also what makes a personalised question share
        # a cache entry with everyone else asking about that city.
        if spec.needs_tool and not params.get("city"):
            stored = memory.home_city(facts)
            if stored:
                params["city"] = stored
            elif cls.intent == "weather_at_home":
                pending.remember(session_id, user_text, cls.intent, params)
                return await finish(ASK_LOCATION, cls=cls)

        # ASK_LOCATION promises "tell me and I'll remember it", and the reply is
        # usually a bare place name rather than "I live in X". Treat a short
        # place-only turn as the answer to that question and store it, or the
        # promise is broken and the next vague turn asks again.
        elif (spec.needs_tool
              and params.get("city")
              and not memory.home_city(facts)
              and len(user_text.split()) <= 3):
            await memory.put(session_id, "home_city", params["city"])
            facts["home_city"] = params["city"]
            # A bare place name is usually the reply to ASK_LOCATION. If a
            # question is waiting on it, answer that instead of the literal
            # "what's the weather there" this turn would otherwise become.
            held = pending.take(session_id)
            if held:
                resumed = await _resume(
                    session_id, held, params["city"], facts, mode, row, trace_id)
                if resumed:
                    return await finish(resumed, route=row.route, cls=cls)

        row.cacheable = (
            trusted
            and spec.cacheable
            and bool(params.get("city") or not spec.needs_tool)
        )

        # --- 5. cache lookup ---------------------------------------------
        # Placed after parameter resolution, because the key needs the resolved
        # city -- "weather at home" only becomes city=dubai here, and that is
        # what lets a personalised question share an entry with everyone else
        # asking about that city.
        #
        # Placed before the tool call, because that is where the saving is: a
        # hit skips the tool *and* the model.
        #
        # Only in `full` mode, so baseline and router bypass it entirely and the
        # comparison stays same-code.
        if row.cacheable and mode == "full" and cache.enabled():
            row.cache_key = cache.build_key(cls.model_version, cls.intent, params)
            hit, similarity = cache.lookup(row.cache_key, user_text)
            # Recorded on a miss too: the threshold sweep replays these scores
            # instead of re-running the workload.
            row.similarity = similarity
            if hit is not None:
                return await finish(hit.answer, cached=True, cls=cls)

        # A horizon the tool cannot serve. Say so rather than answering for
        # today: wrong advice about next week's trip is worse than a limit.
        if params.get("day") == "beyond_forecast":
            return await finish(
                "I can only check today and tomorrow. Ask me again nearer the "
                "time and I'll have the forecast.", cls=cls)

        # --- 6. tool -----------------------------------------------------
        tool_context = ""
        # Freshness is owned by the tool that produced the data. Lifted out of
        # the branch so a cacheable no-tool intent still has a TTL to store with.
        ttl_seconds = intents.ttl_seconds(cls.intent)
        if spec.needs_tool:
            if not params.get("city"):
                # No city in the request and none stored. Ask for the home city
                # rather than for "a city": the answer is worth storing, so the
                # question only ever needs asking once. Hold the question so the
                # reply that supplies the city can also answer it.
                pending.remember(session_id, user_text, cls.intent, params)
                return await finish(ASK_LOCATION, cls=cls)

            result = await weather.fetch(
                params["city"], day=params.get("day", "today"), unit=unit)
            row.tool_called = True
            if not result.usable:
                # A tool failure is answered honestly, never papered over.
                msg = ("I couldn't find that place."
                       if result.error == "unknown_city"
                       else "I couldn't reach the weather service just now.")
                return await finish(msg, cls=cls)
            tool_context = result.context
            if result.freshness_seconds:
                ttl_seconds = result.freshness_seconds

        # --- 6. route and generate ---------------------------------------
        # baseline mode ignores routing so the comparison is same-code.
        tier = "large" if (mode == "baseline" or degrade_tier) else spec.tier

        system = BASE_PROMPT
        if tool_context:
            system += GROUNDING + "\n\nLive data:\n" + tool_context
        elif not spec.needs_tool:
            system += ("\n\nThis turn needs no weather data. Reply "
                       "conversationally and offer to check the weather.")
        if facts.get("unit"):
            system += f"\n\nThe user prefers {facts['unit']}."

        completion = await llm.complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": user_text}],
            tier=tier,
        )
        row.tokens_in, row.tokens_out = completion.tokens_in, completion.tokens_out

        # --- 8. store ----------------------------------------------------
        # Reached only on a successful generation. Every failure path above --
        # tool failure, missing city, memory intents, upstream error -- returns
        # before this point, so none of them can be cached. That is structural
        # rather than a condition that has to remember.
        if row.cacheable and mode == "full" and cache.enabled() and row.cache_key:
            await cache.store_answer(
                row.cache_key, user_text, completion.text, ttl_seconds)

        return await finish(completion.text, route=tier, cls=cls)

    except llm.UpstreamError as exc:
        row.error_code = "upstream_timeout"
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        await request_log.write(row)
        log.warning("upstream failed trace=%s: %s", trace_id, exc)
        raise PipelineError("upstream_timeout", str(exc), trace_id) from exc
