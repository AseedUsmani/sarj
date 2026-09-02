# Agentic Banking Assistant — Technical Design

| | |
|---|---|
| Status | **Design only. Not built.** |
| Updated | 2026-09-01 |
| Related | [PRD](PRD.md) · [Main assignment TDD](../../docs/TDD.md) |

## 1. Architecture

```
                      telephony / web
                             │
                         ASR (in-perimeter)
                             │
                    ┌────────▼────────┐
                    │   Orchestrator  │  ← the product
                    └────────┬────────┘
     ┌──────────────┬────────┼─────────┬──────────────┐
     ▼              ▼        ▼         ▼              ▼
 classifier      cache    planner   entitlements   audit log
  (kNN)        (scoped)  (agent)     (bank IAM)   (append-only)
                             │
                    ┌────────▼────────┐
                    │  tool gateway   │  static allowlist
                    └────────┬────────┘
        ┌───────────┬────────┼────────┬───────────┐
        ▼           ▼        ▼        ▼           ▼
     core        cards     loans     CRM        KYC
    banking
```

Everything inside the perimeter. ASR, TTS and models are either self-hosted or
routed to an in-region provider, depending on the bank's residency position.

## 2. Turn lifecycle

```
1  transcript
2  embed → classify intent + entities            no LLM
3  authenticate caller → customer_id, entitlements
4  if intent ∈ ACTION      → deterministic handler, no model
5  cache lookup (scoped by customer + entitlement)
       HIT → answer, log, done
6  plan: which tools, in what order              base model or static plan
7  tool gateway: allowlist check → entitlement check → call
8  route: base | complex model → generate from tool data
9  cache store (if cacheable), audit write
```

Steps 2–5 are the cheap path. Step 6 only runs when a turn genuinely needs
multiple tools.

## 3. Agent loop, and how much autonomy it gets

The word "agent" here means: the system decides *which of a fixed set of tools*
to call, in what order, to answer a question. It does not mean the model decides
what it is allowed to do.

**Three tiers, chosen by intent class:**

| Tier | Planning | Example |
|---|---|---|
| Static | Fixed tool sequence, no model | "What's my balance" → `core.balance` |
| Guided | Model picks from a shortlist for that intent | "Why was my card declined" → `cards.txn`, `cards.status`, `core.balance` |
| Open | Model plans over the full allowlist, bounded depth | "Explain this charge and what happens next" |

Most volume is static. Open planning is reserved for the tail, is depth-limited,
and every step is logged with its justification.

**Loop bounds:** max 4 tool calls, max 2 planning rounds, hard 6s wall clock.
Exceeding any bound degrades to human handoff rather than continuing.

## 4. Tool gateway

Every tool call passes three gates, in order:

```
allowlist(deployment, tool)      → is this tool enabled for this bank at all?
entitlement(customer, tool, args)→ may THIS customer read THIS account?
budget(turn)                     → within call/latency budget?
```

**No runtime tool discovery.** The allowlist is static per deployment and
versioned. This is deliberately the opposite of the standard agentic pitch: a
regulated deployment must be able to state exactly which operations were
reachable during any given call, and prove it. Discovery makes that unprovable.

**Entitlements are enforced in the gateway, not the prompt.** A model instructed
not to read another customer's account is a control that fails silently. A
gateway that refuses is a control that fails loudly and leaves a log line.

**On MCP.** MCP is the right transport here — it standardises the tool interface
across N banks × M backend systems, and integration cost, not model cost,
dominates these deployments. Adopt the protocol and schema; discard the dynamic
discovery. Protocol standard, static allowlist.

## 5. Caching under entitlements

This is where the weather design does **not** transfer.

| | Weather | Banking |
|---|---|---|
| "at home" → canonical city | Resolves to a shareable key | No analogue |
| Balance | — | Irreducibly per-customer |
| Sharing | Across all users | Never, for account data |

### 5.1 Scopes

| Scope | Key | Shareable | TTL | Invalidation |
|---|---|---|---|---|
| `PRODUCT` | `p:{intent}:{params}` | all customers | 7d | content publish event |
| `CUSTOMER` | `c:{customer_id}:{intent}:{params}` | never | 1h | account event |
| `NONE` | — | — | — | not cached |

Customer identity is in the **key namespace**, so a lookup can only ever search
within one customer's partition. A cross-customer hit is not improbable — it is
unreachable without a key-construction bug, which is unit-tested at similarity
threshold 0.0.

Entity parameters go in the key for the same reason as the weather build:
`IFSC:andheri_east` and `IFSC:andheri_west` have cosine ≈0.96 and different
answers. Threshold tuning cannot separate them; namespacing can.

### 5.2 Invalidation is event-driven

TTL is a backstop. A balance cached for an hour is wrong the instant a
transaction posts.

```
core_banking_events → invalidate(customer_id, account_id)
   DELETE WHERE invalidation_key = 'acct:' || account_id
```

Requires a consumable event stream from core banking. Where none exists —
common — customer-scoped caching is restricted to slow-moving facts (limits,
product holdings, KYC status) and live balances are simply not cached. That is a
deployment-time decision, not a code path.

### 5.3 Approval workflow

Compliance is unlikely to accept a fully learned cache immediately. The design
supports a staged path:

1. **Shadow.** Cache computes hits, serves nothing, logs would-be false hits.
2. **Assisted.** Product-scope entries served only after human approval; the
   approved-answer table banks already maintain becomes the seed corpus.
3. **Measured.** Customer-scope entries served automatically once the false-hit
   bound holds over a review period.

The false-hit curve from the main assignment is the evidence that moves a
deployment from stage 1 to stage 3.

## 6. Model routing

| Route | Model | Use |
|---|---|---|
| None | — | Actions, static-plan lookups answered from a template |
| Base | small | Formatting tool output, procedural answers, memory |
| Complex | large | Disputes, multi-tool reasoning, ambiguous intent |

**Numbers are never model-generated.** Balances, rates and dates are inserted
from tool output via template slots; the model produces the surrounding language.
This removes the highest-severity hallucination class entirely rather than
mitigating it.

Escalation from base to complex on low confidence is cost-tolerant: an escalated
turn pays for both models, so it only becomes net-negative when escalation is
very frequent (`e = 1 − base_cost/complex_cost`). **TODO(compute)** once the tier
gap is known. In practice the binding constraint here is the latency budget on a
live call, not cost.

## 7. Audit

Every turn writes an append-only record: transcript, classified intent, resolved
entities, authenticated identity, entitlement decisions, every tool call with
arguments and results, cache hit or miss with the entry ID, model route, prompt
hash, and final text.

The requirement is reconstruction: given a complaint six months later, show
exactly what was said, on whose authority, from which data. "The model decided
to" is not an acceptable explanation, which is why steps 4 and 7 of the lifecycle
are deterministic and logged rather than reasoned.

## 8. Failure modes

| Mode | Impact | Mitigation |
|---|---|---|
| Cross-customer cache hit | Reportable incident | Customer in key namespace; tested at t=0 |
| Stale balance | Wrong figure quoted | Event invalidation; else don't cache balances |
| Unapproved tool invoked | Compliance breach | Static allowlist in gateway |
| Entitlement bypass | Data exposure | Enforced in gateway, never in prompt |
| Hallucinated figure | Regulatory exposure | Template slots from tool output only |
| Core banking latency spike | Dead air on a call | Per-tool timeout, degrade to handoff |
| Agent loop non-termination | Cost and latency | Hard bounds on depth, calls, wall clock |

## 9. Why this is not built

Three part-time days. Building it would require a bank's systems, an entitlement
service, an event stream, and a compliance counterparty — none of which exist in
an evaluation exercise, and simulating all four would produce numbers that mean
nothing.

The main assignment instead measures the two mechanisms that *are* measurable
against real data, on a real API. This document is the argument for where they go
next, not a claim to have gone there.
