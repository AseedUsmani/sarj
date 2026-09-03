# Sarjy — Product Requirements

Owner: aseedusmani@gmail.com · Updated 2026-09-04 (start of day 2) · [TDD](TDD.md) ·
[Bonus design](../bonus_assignment/docs/PRD.md)

## 1. What

**An MVP.** A voice assistant for weather: browser client, Python service, two
upstream APIs. Talk to it, it answers out loud, remembers your home city and
units, reads live data from Open-Meteo.

Built to demonstrate one idea end to end and measure it. Single instance, no
auth, Chrome only, one tool. Production concerns are named in §4 and designed —
not built — in `bonus_assignment/`.

## 2. Deep dive: caching and routing an expensive upstream

An LLM API is a slow, metered dependency on the request path, and traffic to it is
repetitive and mostly easy. So: cache it, and route cheap work to a cheap tier.
Measured against an all-large-model baseline.

The hard part is the cache key. Callers never phrase a request the same way
twice, so the key can't be the text — but text similarity alone is unsafe, because
"weather in Delhi" and "weather in Mumbai" are different requests that happen to
be worded almost identically. Correctness can't depend on a similarity threshold.

~70% service and orchestration, 20% measurement, 10% frontend. No model training
or prompt research.

Open-Meteo is live, free and keyless, so the numbers come from a real upstream
rather than a simulation, and it keeps both hard cache problems — parameterised
keys and real freshness windows.

## 3. Goals

| # | Goal | Metric |
|---|---|---|
| G1 | Cut LLM cost per conversation | ≥60% vs baseline — requires ~36% cache hit rate at measured rates |
| G2 | Don't lose quality | ≥95% of baseline, judge-scored, n=50 |
| G3 | Bound cache error | False-hit rate <1% |
| G4 | Cut median latency | p50 −25%, against a per-stage budget |

G3 gates G1. A cost win with an unmeasured error rate isn't a result.

## 4. Non-goals

Auth, multi-user, PII, audit trails, telephony, non-English, non-Chrome,
horizontal scaling, migrations, staging. An MVP in three part-time days.

Retries *are* in scope and built (TDD §6.2) — two third-party upstreams sit on
the request path, and a transient connection failure returning a 503 to someone
speaking to the assistant is not an acceptable MVP.

## 5. Requirements

| ID | Requirement | Priority |
|---|---|---|
| R1 | Voice in, spoken out, in-browser | P0 |
| R2 | Facts persist across sessions (home city, units), with a stated conflict rule | P0 |
| R3 | Live Open-Meteo data | P0 |
| R4 | Deployed, publicly reachable | P0 |
| R5 | Router: small vs large tier | P0 |
| R6 | Semantic cache with parameterised keys | P0 |
| R7 | Baseline mode selectable at runtime | P0 |
| R8 | Per-request log: route, tokens, latency, cache outcome | P0 |
| R9 | Threshold sweep: hit-rate and false-hit curves | P0 |
| R10 | Recorded tool fixtures, so evaluation runs are not confounded by live weather | P0 |
| R11 | Bounded retries on transient upstream failures | P0 |
| R12 | Cache flush endpoint | P1 |
| R13 | Barge-in | P2 |

R7 exists because the cost claim is meaningless without a same-code comparison.

## 6. Success metrics

Over a generated workload of ≥400 requests across ~25 intents.

| Metric | Baseline | Target |
|---|---|---|
| LLM cost per 1,000 requests | TODO (day 1) | TODO — as % of baseline |
| Judge quality (n=50) | 1.00 ref | ≥0.95 |
| False-hit rate | — | <1% |
| Hit rate (of addressable) | 0% | TODO — after context-free share is known |
| p50 latency | TODO (day 1) | −25% |

Absolute targets are unset until the day-1 baseline. Cost means **LLM cost only**
— hosting and database are fixed, not per-request.

**Risk position — revised 2026-09-03, see [FINDINGS](FINDINGS.md).** Measured
rates show the tier gap is 2×, not the ~12× assumed. Routing alone yields only
37–43%, so **G1 now depends on the cache**, which needs ~36% hit rate to close the
gap. The risk is concentrated in the cache rather than hedged away from it.

## 7. Scope decisions

| Decision | Why |
|---|---|
| ~25 intents | Zipf skew is realistic at 25; the taxonomy is the expensive part |
| In-process numpy, not pgvector | <2k entries; exact search, no network hop |
| Judge 50 sampled responses | Enough for a quality number with a declared sample |
| No escalation path | Break-even is 50% escalation at measured rates, and tiers are latency-indistinguishable, so there is no latency upside either |
| Single instance | Two replicas would split the cache and void the measurement |

## 8. Milestones

| Day | Exit criteria |
|---|---|
| 1 | PRD, TDD, deep dive chosen with reasons |
| 2 | Floor working end to end, deployed, cache serving hits |
| 3 | Cache measured; findings written; deck under 5:00 |

Deployment and the cache sit on day 2 so day 3 is measurement and presentation
rather than building. Cut order fixed in advance — see TDD §14.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Workload ground truth wrong → every safety number wrong | Build first; hand-check 30 labels |
| Embedding model exceeds 512MB host | int8 ONNX (~23MB); validate day 1 |
| Cold start (30–60s) kills the demo | Keep-warm ping; open URL an hour before |
| Deployment eats day 1 | Deploy an empty service before any features |

## 10. Bonus deliverable

`bonus_assignment/` holds a design-only PRD and TDD for the production version of
this idea: a read-only multi-service agent for a bank. Not built. It shows where
the architecture goes with twenty tools, real entitlements, and a false cache hit
that is a reportable incident rather than a wrong temperature.

## 11. Open questions

1. Groq token pricing — look up and cite with a date. Cost figures are blocked on it.
2. Is context-free traffic above 60%? Below that, cache upside is capped.
