# Sarjy — Product Requirements

| | |
|---|---|
| Owner | support@gamorite.com |
| Status | Approved for build |
| Updated | 2026-09-01 |
| Related | [TDD](TDD.md) · [Bonus: agentic banking assistant](../bonus_assignment/docs/PRD.md) |

## 1. What

Sarjy: a voice assistant for weather. Talk to it, it answers out loud, remembers
your home city and unit preference, and reads live data from Open-Meteo.

## 2. Deep dive: cost per conversation

Voice AI is priced per conversation; margin is `price − (ASR + LLM + TTS)`. Every
turn today costs the same regardless of difficulty — "temperature in Delhi" bills
like "should I move my flight because of the storm."

Two mechanisms, measured against an all-large-model baseline:

1. **Model routing.** Lookups go to a small model, reasoning to a large one.
2. **Semantic cache.** Repeated questions, asked differently, skip the model.

Weather is the build domain because Open-Meteo is live, free and keyless, so
every number is measured against real data rather than a simulation. It also
keeps the two hard caching problems: parameterised entities (Delhi vs Mumbai) and
real freshness limits.

## 3. Goals

| # | Goal | Metric |
|---|---|---|
| G1 | Cut LLM cost per conversation | ≥60% vs baseline *(provisional — revisit after day-1 baseline)* |
| G2 | Don't lose quality | ≥95% of baseline, judge-scored |
| G3 | Bound cache error | False-hit rate <1% |
| G4 | Cut median latency | p50 −25% |

G3 gates G1. A cost win with an unmeasured error rate isn't a result.

## 4. Non-goals

Auth, multi-user, PII handling, audit trails, retries, telephony, non-English,
non-Chrome. Three part-time days.

## 5. Requirements

| ID | Requirement | Priority |
|---|---|---|
| R1 | Voice in, spoken out, in-browser | P0 |
| R2 | Facts persist across sessions (home city, units) | P0 |
| R3 | Live Open-Meteo data | P0 |
| R4 | Deployed, publicly reachable | P0 |
| R5 | Model router: small vs large | P0 |
| R6 | Semantic cache with entity-aware keys | P0 |
| R7 | Baseline mode selectable at runtime | P0 |
| R8 | Per-turn log: route, tokens, latency, cache outcome | P0 |
| R9 | Threshold sweep: hit-rate and false-hit curves | P0 |
| R10 | Cache flush endpoint | P1 |
| R11 | Barge-in | P2 |

R7 exists because the cost claim is meaningless without a same-code comparison.

## 6. Success metrics

Over a generated workload of ≥400 turns across ~25 intents.

| Metric | Baseline | Target |
|---|---|---|
| LLM cost per 1,000 turns | TODO (day 1) | TODO — set as % of baseline |
| Judge quality (n=50) | 1.00 ref | ≥0.95 |
| False-hit rate | — | <1% |
| Hit rate (of addressable) | 0% | TODO — set after context-free share is known |
| Context-free share | — | measured, not targeted |
| p50 latency | TODO (day 1) | −25% |

**Absolute cost targets are deliberately unset.** An earlier draft carried
projected dollar figures built on assumed prompt sizes, assumed hit rates and
unverified token pricing. They have been removed rather than dressed up: the
baseline is measured on day 1, and targets are expressed as a percentage of it.

Cost here means **LLM cost only** — hosting, database and embedding CPU are
excluded and are fixed rather than per-turn.

**Risk position:** the cache hit rate is the most uncertain input and the one G1
least depends on. Routing alone is expected to capture most of the available
saving, since the price gap between model tiers is large and most traffic is
lookups. **TODO(validate):** confirm that routing alone clears G1 once rates are
known; if it does not, reset G1 against measured data rather than defending a
number chosen in advance.

## 7. Milestones

| Day | Exit criteria |
|---|---|
| 0 | Docs reviewed; repo + draft PR open; Groq key |
| 1 | R1–R4, R7 live on the deployed URL; baseline recorded |
| 2 | R5, R6, R8, R9 done; sweep generated |
| 3 | Metrics populated; findings written; deck under 5:00 |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Workload ground truth wrong → every safety number wrong | Build first; hand-check 30 labels |
| Embedding model exceeds 512MB host | int8 ONNX (~23MB); validate day 1 |
| Free-tier cold start (30–60s) kills the demo | Keep-warm ping; open URL 1h before |
| Deployment eats day 1 | Deploy an empty service before any features |

## 9. Bonus deliverable

`bonus_assignment/` contains a design-only PRD and TDD for the production version
of this idea: a multi-service agentic assistant for a bank. Not built. It exists
to show where this architecture goes when there are twenty tools, real
entitlements, and a false cache hit is a reportable incident rather than a wrong
temperature.

## 10. Open questions

1. **Groq token pricing** — look up and cite with a retrieval date. Every cost
   figure in the TDD is blocked on this.
2. Is context-free traffic above 60%? Below that, cache upside is capped.
