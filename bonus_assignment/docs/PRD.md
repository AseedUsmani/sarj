# Agentic Banking Assistant — Product Requirements

| | |
|---|---|
| Status | **Design only. Not built.** |
| Updated | 2026-09-01 |
| Related | [TDD](TDD.md) · [Main assignment](../../docs/PRD.md) |

## 1. Why this document exists

The main assignment demonstrates semantic caching and model routing on a weather
assistant with one tool and no stakes. This describes the same mechanisms in a
bank, where the constraints are entirely different and mostly non-technical.

It is unbuilt by choice. Three part-time days buys one measured result, not a
regulated multi-service agent.

## 2. Problem

A retail bank's contact centre handles high call volume dominated by a few
hundred intents. Human handling is expensive; deflection to an assistant is the
value proposition, and margin is `price_per_call − inference − telephony`.

But a banking assistant cannot be a single model with a prompt:

- Answers come from many systems — core banking, cards, loans, CRM, KYC — each
  with its own owner, latency and failure behaviour.
- Every read must be authorised for *this* customer on *this* authenticated call.
- Every action must be auditable and explainable to a regulator.
- Customer data cannot leave the bank's perimeter.

## 3. Goals

| # | Goal | Metric |
|---|---|---|
| G1 | Deflection without human handoff | ≥60% of eligible calls |
| G2 | Cost per handled call | TODO — as % of an all-complex-model baseline |
| G3 | Zero cross-customer data exposure | 0 incidents; provable by construction |
| G4 | Auditability | 100% of turns reconstructable from logs |
| G5 | Latency | p95 time-to-first-audio <1.5s |

G3 and G4 are not optimisations. They are preconditions for deployment.

## 4. Users

| User | Need |
|---|---|
| Customer | Account state, product info, procedural help, actions |
| Contact centre agent | Warm handoff with full context |
| Bank operations | Deflection rate, cost per call, containment |
| Compliance / audit | Evidence of what was said, why, and on whose authority |
| Bank IT | Deployment inside the perimeter, integration with existing IAM |

Compliance is a first-class user. In the weather build there is no analogue.

## 5. Capabilities

| ID | Capability | Priority |
|---|---|---|
| C1 | Multi-service agent loop with tool planning | P0 |
| C2 | Entitlement check on every tool call, server-side | P0 |
| C3 | Deterministic handlers for all state-changing intents | P0 |
| C4 | Per-customer cache with event-driven invalidation | P0 |
| C5 | Model routing: base / complex / no-model | P0 |
| C6 | Full turn audit trail: inputs, tools, authority, output | P0 |
| C7 | Human handoff with context transfer | P1 |
| C8 | Bounded tool allowlist per deployment, no runtime discovery | P0 |
| C9 | PII redaction before any egress | P0 |

C8 is deliberately the opposite of the usual agentic pitch. See TDD §4.

## 6. Non-goals

Open-ended agentic autonomy, model-initiated actions, runtime tool discovery,
cross-customer analytics, and answering anything requiring regulated financial
advice.

## 7. Economics

The same asymmetry as the weather build, at higher volume:

| Path | Relative cost |
|---|---|
| Cached answer | 0 |
| Deterministic handler | 0 |
| Base model | 1× |
| Complex model | TODO — measure the tier gap |
| Human agent | orders of magnitude above all of the above |

**TODO(pricing):** the base-to-complex multiple depends on the models a given
bank's residency position actually permits, which may be self-hosted rather than
API-served. Self-hosting changes the shape entirely — cost becomes GPU capacity
and utilisation rather than per-token, and the routing argument has to be
re-made against amortised hardware rather than marginal tokens.

Two levers stack: deflection moves calls off humans, and routing plus caching cuts
the cost of the calls that remain. The second only matters at volume — which is
exactly where banks operate.

Note this excludes ASR, TTS and telephony, which in a real deployment are a
significant line item and may exceed LLM cost on short calls.

**What banks already do:** top intents get human-authored, compliance-approved
answers; the model handles the tail. That is a hand-built cache with an approval
workflow. This design automates the caching and *keeps* the approval workflow,
because the approval step is the control, not the overhead.

## 8. Constraints that dominate the design

| Constraint | Consequence |
|---|---|
| Data residency (RBI) | Provider choice restricted; may force self-hosted models |
| No customer data to vendor cloud | Deploy into bank VPC or on-prem |
| Legacy core banking | Slow, rate-limited reads under a conversational latency budget |
| Entitlements | Authorisation per customer per account, not per application |
| Auditability | Model reasoning is not an acceptable explanation for an action |

These, not model quality, determine whether the product ships.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Cached answer crosses customers | Customer identity in the cache key namespace; unreachable, not unlikely |
| Stale balance quoted | Event-driven invalidation; TTL as backstop only |
| Model invokes an unapproved tool | Static allowlist; no runtime discovery |
| Core banking latency breaks the budget | Aggressive per-tool timeouts, degrade to handoff |
| Hallucinated financial figure | Tool data or refusal; never model-generated numbers |

## 10. What would need proving before build

1. Context-free share of real call traffic — determines cache ceiling.
2. Whether entitlement checks fit the latency budget when chained.
3. Whether compliance will accept a learned cache with a measured false-hit
   bound, or requires human approval per entry.

Question 3 is the one that decides whether this architecture is viable at all.
