# Sarjy

A voice assistant for weather, and a semantic cache + model router in front of the
LLM that powers it.

**Live demo:** TODO — deployed URL
**Walkthrough:** TODO — Loom link

---

## What this is

Take-home for Sarj. The assignment asks for a working voice assistant plus one
deep dive taken seriously.

**The assistant** (the required floor): talk to it in the browser, it answers out
loud, remembers your home city and units across sessions, and reads live weather
from Open-Meteo.

**The deep dive** (where the work is): an LLM API is a slow, metered dependency on
the request path, and traffic to it is repetitive and mostly easy. So cache it,
route cheap work to a cheap tier, and measure both against an
all-large-model baseline.

The hard part is the cache key. Callers never phrase a request the same way twice,
so the key can't be the request text — but keying on text *similarity* alone is
unsafe, because "weather in Delhi" and "weather in Mumbai" are different requests
that happen to be worded almost identically. Cache correctness can't depend on a
similarity threshold. Identifying parameters go in the key instead, and similarity
only resolves phrasing within a namespace.

Roughly 70% backend and orchestration, 20% measurement, 10% frontend. No model
training or prompt research — the model is a dependency behind a contract, like
any other paid API.

## Results

TODO — populated on day 3 from `request_log`:

| Metric | Baseline | With cache + routing |
|---|---|---|
| LLM cost per 1,000 requests | TODO | TODO |
| p50 latency | TODO | TODO |
| Cache hit rate (of addressable traffic) | 0% | TODO |
| False-hit rate at shipped threshold | — | TODO |
| Answer quality vs baseline (judge, n=50) | 1.00 | TODO |

## Why Open-Meteo

It needs no key and no signup, so this repo runs for a reviewer immediately and
nothing in the evaluation depends on a credential I'd have to share. It returns in
well under a second, so it doesn't distort the latency measurements the deep dive
is about. And weather is the clearest case of an assistant needing live data
rather than guessing — it also happens to preserve both hard caching problems,
parameterised keys ("Delhi" vs "Mumbai") and real freshness windows (a forecast is
correct for minutes, not days).

## A note on the documents

The PRD and TDDs are deliberately over-explicit — full schemas, typed contracts,
exact identifiers, named units, stated failure behaviour on every path. That is
not thoroughness for its own sake. These documents are written to be handed to an
AI coding assistant as well as read by a person, and **anything left implicit is
where a model invents something plausible**.

An unnamed column becomes a guessed column name. An unstated fallback becomes a
confident improvisation. A missing unit becomes milliseconds when you meant
seconds. None of these fail loudly — they produce working code that does the
wrong thing, which is the expensive kind of wrong.

Two habits follow:

- **Unknowns are marked `TODO(...)` rather than filled in.** A plausible
  placeholder is indistinguishable from a measured value once written down. Cost
  projections in an early draft were removed for exactly this reason, once it was
  clear they rested on assumed prompt sizes and unverified pricing.
- **Every failure path states what happens**, not just that it is handled. "Tool
  timeout → serve a stale entry if present, else say the service is unreachable,
  never fabricate" leaves nothing to fill in.

Tables and code blocks are load-bearing for the same reason. Prose is kept to
where it carries a decision or a rationale a table cannot.

## Layout

```
docs/                    the built system
  PRD.md                 product requirements, goals, scope
  TDD.md                 architecture, cache design, measurement plan
bonus_assignment/        design only — not built
  README.md
  docs/PRD.md            agentic banking assistant: product
  docs/TDD.md            ... and its architecture
```

`bonus_assignment/` is where this architecture goes in production: a read-only
multi-service agent for a bank, with twenty tools, tiered authentication, real
entitlements, and a false cache hit that is a reportable incident rather than a
wrong temperature. It is a design deliverable, not code, and the documents say so
throughout.

## Status

| | |
|---|---|
| PRD, TDD, bonus design | done |
| Service, cache, router | not started |
| Deployment | not started |
| Measurement run | not started |

## Running it

TODO — once the service exists:

```bash
cp .env.example .env        # GROQ_API_KEY, DATABASE_URL
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Notes and limitations

- **Chrome only.** Voice uses the browser's Web Speech API, which is the
  assignment's suggested approach and keeps audio out of the backend entirely.
- **Single instance, deliberately.** Two replicas would hold two independent
  caches, halving hit rate and making the measurement meaningless.
- **No auth.** `session_id` is a client-generated string, so memory is
  per-browser, not per-person.
- **Free tier.** The host sleeps after ~15 minutes idle; a cold start is 30–60s.
- Cost figures are LLM cost only; hosting and database are fixed, not per-request.

Unknowns are marked `TODO(...)` in the docs rather than filled with plausible
placeholders — the same reason cost projections were removed once it was clear
they rested on assumed prompt sizes and unverified pricing.
