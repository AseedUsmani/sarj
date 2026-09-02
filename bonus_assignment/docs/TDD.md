# Agentic Banking Assistant — Technical Design

**Design only. Not built.** · Updated 2026-09-01 · [PRD](PRD.md) ·
[Main assignment](../../docs/TDD.md)

> Same convention as the main docs: explicit enough for AI-assisted
> implementation, with unknowns marked `TODO(...)` rather than guessed.

The main assignment builds a cache and a router in front of one upstream, with
one tool and no stakes. This is where that architecture goes when there are
twenty backend systems, real entitlements, a regulator, and a false cache hit is
a reportable incident rather than a wrong temperature.

---

## 1. Architecture

```
              telephony / web ──► ASR (in perimeter)
                                      │
                            ┌─────────▼─────────┐
                            │   Orchestrator    │   ← the product
                            └─────────┬─────────┘
        ┌──────────┬──────────┬───────┴───┬──────────┐
   classifier    cache     planner   entitlements  audit log
  (AI/DS dep)  (scoped)    (agent)    (bank IAM)  (append-only)
                                │
                        ┌───────▼────────┐
                        │  tool gateway  │  static, read-only allowlist
                        └───────┬────────┘
       ┌──────────┬─────────┬───┴────┬─────────┐
  core banking   cards    loans     CRM       KYC
```

Everything runs inside the bank's perimeter. ASR, TTS and models are self-hosted
or in-region depending on the bank's data residency position — which is a
procurement fact, not an engineering preference, and it constrains the rest.

**The orchestrator is the product.** ASR, TTS, models and core systems are all
either bought or already owned. What the vendor builds is the thing in the middle
that sequences them safely.

## 2. Turn lifecycle

```
 1  transcript from ASR
 2  classify        → intent, entities, confidence, version    AI/DS dependency
 3  authenticate    → assurance level, customer_id, entitlements  (§4)
 4  if state-changing → hand off, stop. The assistant does not act.  (§5)
 5  cache lookup    → scoped by customer + entitlements        (§5)
        HIT → answer, audit, done
 6  plan            → which read tools, in what order          (§3)
 7  gateway         → allowlist → entitlement → budget → call  (§5)
 8  route           → base | complex model, generate from tool data  (§7)
 9  cache store (if cacheable), audit write                    (§5, §6)
```

Steps 2–5 are the cheap path and cover most volume. Step 6 runs only when a turn
genuinely needs multiple systems.

### 2.1 Latency budget

A voice call is unforgiving: beyond roughly 1.5s of silence the caller starts
talking again. The budget has to be allocated, not hoped for.

| Stage | Target | Notes |
|---|---|---|
| ASR finalisation | ~300ms | Endpointing dominates; largely bought |
| Classify | ≤20ms | Contract term (§3.2) |
| Authenticate | ≤50ms | Established per call, not per turn (§4.4) |
| Cache lookup | ≤10ms | Local index |
| Tool calls | ≤600ms | The risk; legacy systems are the long pole |
| Model generation | ≤500ms | Base tier; complex tier is roughly double |
| TTS first audio | ~200ms | Streamed |

**TODO(measure):** real core banking read latency per system. This is the number
most likely to break the budget, and it is knowable only inside a given bank.

The mitigation is structural: tool calls fan out in parallel where the plan
allows, and any system missing its timeout is dropped from the answer rather than
waited on.

## 3. Agent loop, and how much autonomy it gets

"Agent" here means the system decides *which of a fixed set of read tools* to
call, and in what order. It does not mean the model decides what it is allowed
to do.

### 3.1 Three planning tiers

| Tier | Planning | Example | Share of volume |
|---|---|---|---|
| Static | Fixed sequence, no model | "What's my balance" → `core.balance` | majority |
| Guided | Model picks from a shortlist bound to that intent | "Why was my card declined" → {`cards.txn`, `cards.status`, `core.balance`} | some |
| Open | Model plans over the full read allowlist, depth-bounded | "Explain this charge and what happens next" | tail |

Most traffic never reaches a planner. Open planning is reserved for the tail, is
depth-limited, and every step is logged with the justification the model gave for
taking it.

**Loop bounds:** at most 4 tool calls, 2 planning rounds, 6s wall clock. Breaching
any bound degrades to human handoff rather than continuing — an agent that keeps
trying is worse than one that gives up quickly, because the caller is waiting in
silence.

### 3.2 The classifier is a dependency

As in the main build, intent and entity resolution sit behind a contract owned by
AI/DS. This design specifies the contract and the failure behaviour, not the
implementation.

```python
@dataclass(frozen=True)
class Classification:
    intent: str
    entities: dict[str, str]
    confidence: float
    model_version: str
```

The implementation choice does diverge from the main build — several hundred
overlapping banking intents is where hand-written rules stop scaling and a
learned classifier earns its cost — but that is their call behind the same
contract.

**What changes here is the cost of being wrong.** In weather a misclassification
yields an unhelpful answer. Here it can route a customer down the wrong tool path
or, far worse, mark a state-changing request as a read. So:

- Confidence floors are higher, and a low-confidence turn degrades to **human
  handoff**, not to a more expensive model.
- Any intent classifying *near* the state-changing set is treated as
  state-changing. Fail toward handoff.
- `classifier_version` is part of the cache key and is recorded in the audit
  trail, so a past turn can be explained against the version that produced it.

## 4. Identity and authentication

Referenced at step 3 of the lifecycle; specified here because on a voice channel
it is the hardest control to get right and it gates everything downstream.

**The assistant never authenticates anyone.** It delegates to the bank's existing
IAM and receives an assertion. Same principle as §5 — the orchestrator holds no
credential, runs no check of its own, and cannot grant itself access. It consumes
a decision made elsewhere.

### 4.1 Assurance levels

Authentication on a phone call is not binary. Different methods carry different
confidence, and the design treats the result as a *level*, not a boolean.

| Level | Established by | Confidence | Unlocks |
|---|---|---|---|
| `L0` anonymous | Nothing | — | Public product info, branch hours, rates |
| `L1` asserted | Caller ID / ANI match to a customer record | Weak — ANI is spoofable | Generic account status ("you have 2 active cards"), no figures |
| `L2` verified | Voice biometric, or KBA, or an app session token | Moderate | Balances, transactions, statements |
| `L3` stepped-up | OTP to registered device, or in-app confirmation | Strong | Anything state-changing — which the assistant hands off anyway (§5) |

**ANI alone never reaches L2.** Caller ID is trivially spoofed, and treating a
matching number as identity is the classic failure in phone banking. It narrows
who the caller claims to be; it does not verify it.

Web and in-app channels start at `L2` because the session token already carries a
verified identity — the assistant inherits it rather than re-establishing it.

### 4.2 Level gates the data, not the conversation

A turn is not rejected for insufficient assurance. It is answered at the level
available, with an offer to step up.

```
"what's my balance"  at L1
  → do not answer, do not refuse outright
  → "I can get that once I verify it's you — I'll send a code to your
     registered number."
  → step-up runs in the bank's flow; assistant resumes at L2
```

Every tool declares the minimum level it requires (`ToolSpec.min_assurance`,
§5.1), and the gateway enforces it alongside the entitlement check. A tool that
would return figures cannot be invoked at `L1`, regardless of what the planner
decided.

### 4.3 Assurance is part of the cache key

The sharp consequence, and easy to miss: **an answer computed at `L2` must never
be served to an `L1` caller.**

```
c:{customer_id}:{assurance_level}:{version}:{intent}:{params}
```

Without the level in the key, a warm cache becomes an authentication bypass — the
expensive path checks assurance, the cheap path skips it and returns the answer
anyway. This is exactly the class of bug that makes caches dangerous in regulated
systems, and it is prevented by namespacing rather than by remembering to check.

### 4.4 Session binding and duration

Authentication is established **per call**, not per turn — re-verifying every turn
would destroy the latency budget (§3.1). The assertion is bound to the call leg
and expires with it.

- A transferred or re-established call re-authenticates. An assertion does not
  survive a channel change.
- Assurance can only be *raised* mid-call, never inherited from a prior call.
- Long calls carry a maximum assertion lifetime; beyond it, sensitive intents
  require step-up again.

### 4.5 Failure and degradation

| Situation | Behaviour |
|---|---|
| IAM unavailable | Serve `L0` only; offer handoff. Never assume identity |
| Biometric inconclusive | Stay at current level; offer step-up |
| Repeated failed step-up | Stop offering, hand off to a human agent |
| Caller declines to verify | Answer at current level, say plainly what needs verification |

Every path fails toward *less access*, never toward assumed identity.

### 4.6 What gets audited

Method, resulting level, timestamp, and every level change during the call, plus
the assertion id issued by IAM. A later question is not "was this person
authenticated" but "to what level, by what method, and what did that let them
see" — which requires the level, not just the outcome.

## 5. The assistant is read-only

This is the central safety property of the design.

**The tool allowlist contains read operations only.** The assistant cannot
execute a transfer, block a card, raise a dispute or change a profile, because no
such tool exists in the surface it can reach. Not "the model is instructed not
to" — the capability is absent.

```
"block my card"
  → classify: intent = block_card  (state-changing)
  → assistant STOPS. No tool call, no model generation about the action.
  → hand off: authenticated app flow, IVR with step-up auth, or human agent
  → assistant may confirm the handoff occurred; nothing further
```

Three consequences worth stating plainly:

- **A whole class of failure becomes impossible rather than mitigated** — a
  hallucinated amount, a misheard payee, a retried transfer executing twice.
- **Step-up authentication belongs to the receiving flow**, not the assistant.
  Voice biometrics and ASR confidence are not sufficient authority to move money,
  and it would be tempting to argue otherwise.
- **The agent loop is read-only**, so its retries and fan-out are safe by nature
  rather than by discipline (§8.1).

**The cost, stated honestly.** Genuinely urgent cases — card blocking above all —
become a handoff rather than a completion. That is the right trade for a first
deployment: deflection value is overwhelmingly in reads, and the tail risk of
writes is unbounded. Revisit only with an explicit mandate, step-up auth, and a
confirmation protocol.

### 5.1 The tool gateway

Every call passes three gates, in order, and the order matters:

```
allowlist(deployment, tool)        is this tool enabled for this bank at all?
assurance(level, tool)             is the caller verified strongly enough?   (§4.2)
entitlement(customer, tool, args)  may THIS customer read THIS account?
budget(turn)                       within the call's remaining time and cost?
```

**No runtime tool discovery.** The allowlist is static per deployment and
versioned. This is deliberately the opposite of the usual agentic pitch: a
regulated deployment must be able to state exactly which operations were
reachable during any given call, and prove it. Discovery makes that unprovable.

**Entitlements are enforced in the gateway, never in the prompt.** A model
instructed not to read another customer's account is a control that fails
silently. A gateway that refuses fails loudly and leaves a log line. The
entitlement decision, not just the outcome, is written to the audit trail.

**Tool contract.** Each tool declares its own metadata rather than the
orchestrator hard-coding assumptions:

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    read_only: bool          # gateway refuses to register False
    freshness_seconds: int   # drives cache TTL — the tool owns its own staleness
    timeout_ms: int
    entitlement_scope: str   # what the caller must hold to invoke it
    min_assurance: str       # L0 | L1 | L2 | L3 — enforced by the gateway (§4.2)
    cache_scope: Literal["PRODUCT", "CUSTOMER", "NONE"]
```

`read_only` being a registration-time check is what makes §4 enforceable rather
than aspirational.

### 5.2 On MCP

MCP is the right transport here. It standardises the tool interface across N banks
× M backend systems, and **integration cost, not model cost, dominates these
deployments** — that is the argument that would actually get funded.

Adopt the protocol and the schema; discard the dynamic discovery. Protocol
standard, static allowlist. The value is in not rewriting the adapter layer for
every client, not in letting a model find new capabilities at runtime.

## 6. Caching under entitlements

Where the main build's design does **not** transfer.

Weather resolves "at home" to a canonical city, so a personalised question becomes
a globally shareable cache entry. A balance is irreducibly per-customer — there
is no canonical parameter to resolve to, so the key must carry the customer and
the entry can never be shared. That is the sharpest structural difference between
the two domains.

### 6.1 Scopes

| Scope | Key | Shareable | TTL | Invalidation |
|---|---|---|---|---|
| `PRODUCT` | `p:{version}:{intent}:{params}` | all customers | 7d | content publish event |
| `CUSTOMER` | `c:{customer_id}:{version}:{intent}:{params}` | never | 1h | account event |
| `NONE` | — | — | — | not cached |

Customer identity is in the **key namespace**, not a filter applied after
retrieval. A lookup can only ever search within one customer's partition, so a
cross-customer hit is not improbable — it is unreachable without a
key-construction bug, which is unit-tested at similarity threshold 0.0.

Entity parameters go in the key for the same reason as the main build:
`IFSC:andheri_east` and `IFSC:andheri_west` are different requests with different
answers, and correctness must not depend on an embedding model distinguishing
them. Namespacing makes the collision impossible; tuning only makes it unlikely.

### 6.2 Invalidation is event-driven

TTL is a backstop, not the mechanism. A balance cached for an hour is wrong the
instant a transaction posts.

```
core_banking_events → invalidate(customer_id, account_id)
    DELETE FROM cache_entries WHERE invalidation_key = 'acct:' || account_id
```

This requires a consumable event stream from core banking. **Where none exists —
common — customer-scoped caching is restricted to slow-moving facts** (limits,
product holdings, KYC status) and live balances are simply not cached. That is a
deployment-time configuration decision, not a separate code path, and it is the
kind of constraint that decides how much of the cost saving is actually available
at a given bank.

### 6.3 Cache entries are customer records

An entry contains customer data. It inherits the same retention, encryption and
deletion obligations as any other store of customer data — including
right-to-be-forgotten. It is not a derived convenience that can be exempted, and
treating it as one is a compliance finding waiting to happen.

### 6.4 Staged approval

Compliance is unlikely to accept a fully learned cache on day one. The design
supports a path rather than demanding trust up front:

1. **Shadow.** The cache computes hits and serves nothing. Would-be false hits are
   logged and reviewed.
2. **Assisted.** `PRODUCT` scope served only after human approval. The
   approved-answer table the bank already maintains becomes the seed corpus —
   this is the step where the existing manual process is absorbed rather than
   replaced.
3. **Measured.** `CUSTOMER` scope served automatically once the false-hit bound
   holds over a review period.

**The false-hit curve produced by the main assignment is exactly the evidence
that moves a deployment from stage 1 to stage 3.** That is the connection between
the two directories.

## 7. Routing and generation

| Route | Model | Use |
|---|---|---|
| None | — | Static lookups answered from a template |
| Base | small | Formatting tool output, procedural answers |
| Complex | large | Disputes, multi-tool reasoning, ambiguous intent |

Routing reuses the intent from §3.2, so it costs nothing additional.

**Numbers are never model-generated.** Balances, rates, dates and account numbers
are inserted from tool output through template slots; the model produces only the
surrounding language.

```
template: "Your {account_label} balance is {balance} as of {as_of}."
slots filled from tool output, never from generation
```

This removes the highest-severity hallucination class entirely rather than
mitigating it. A model that cannot emit a figure cannot emit a wrong figure.

Escalation from base to complex on low confidence is cost-tolerant — an escalated
turn pays both tiers, so it is only net-negative when escalation is very frequent
(`e = 1 − base/complex`), **TODO(compute)** once the tier gap is known. Here the
binding constraint is the latency budget on a live call, not cost.

## 8. Cross-cutting concerns

The agent loop changes how several of these behave, so they are specified rather
than assumed.

### 8.1 Idempotency

Because the allowlist is read-only (§5), the dangerous case — a retried write
executing twice — cannot arise on the assistant path. Reads are idempotent by
nature, so the loop's retries are safe without further machinery.

Idempotency remains a requirement of the **receiving** flows that handle state
changes after handoff, but those are existing bank systems with their own
controls and are outside this design.

This is the clearest example of the read-only constraint removing a problem
rather than managing one.

### 8.2 Rate limiting

Three limits, three different reasons:

| Limit | Scope | Protects against |
|---|---|---|
| Per customer | calls and turns per minute | Abuse, runaway loops on one session |
| Per tool | concurrent calls to each backend | Overwhelming fragile core banking |
| Per tenant | aggregate inference spend | One bank's traffic starving another |

The per-tool limit matters most and is the one usually missed. Core banking is
typically sized for batch and screen-driven traffic, not for an assistant fanning
out several reads per conversational turn across thousands of concurrent calls.
**The assistant can take down channels it has nothing to do with.** The gateway is
the natural throttle point because every call already passes through it.

### 8.3 Timeouts, retries, circuit breaking

Per-tool timeouts sized to the conversational budget (§2.1), not to the backend's
comfort. A read taking four seconds has already failed from the caller's point of
view, whatever the backend thinks.

Retries: at most one, only on connection-level failures, only for reads.

Circuit breaker per tool. When a backend trips, intents depending on it degrade
to human handoff rather than stalling the call. **Which intents are blocked by
which tool is declared**, so degradation is predictable and can be rehearsed
rather than discovered during an incident.

### 8.4 Multi-tenancy

The vendor runs this for several banks. Tenancy is a **deployment boundary, not a
row-level filter**: separate instance, separate database, separate tool allowlist,
separate model credentials per bank. Cache entries cannot cross a tenant because
they never share a process.

Heavier than logical isolation, and the correct default — most banks would not
accept sharing infrastructure with another bank regardless of the guarantees
offered, and arguing the point is not a good use of a sales cycle.

### 8.5 Secrets, transport, storage

Credentials come from the bank's own vault, not application config. TLS on every
hop including in-perimeter ones. Transcripts and cache entries encrypted at rest.
PII redacted before any egress, including to logs and telemetry.

### 8.6 Observability, distinct from audit

Audit (§9) answers "what happened on this call" for a regulator. Operational
telemetry answers "is the system healthy" for an on-call engineer. Different
stores, different retention, different consumers.

Minimum: per-tool latency and error rate, cache hit and false-hit rate in shadow,
routing split, escalation rate, loop-bound violations, and **handoff rate by
cause** — which is the leading indicator, because it moves before customers
complain.

### 8.7 Consent, recording and disclosure

Calls are recorded and processed by an automated system, and both facts usually
require disclosure. The opening turn carries the notice, consent is recorded in
the audit trail, and a refusal routes to a human rather than continuing silently.

Recordings and transcripts are customer data on the same footing as cache entries
(§6.3): encrypted, retained to a stated schedule, and reachable by a deletion
request.

### 8.8 Kill switches

Three independent controls, because they answer different incidents:

| Switch | Effect | Used when |
|---|---|---|
| Cache disable | All lookups miss; everything computes fresh | A poisoned or suspect entry class |
| Model disable | Static and template answers only, else handoff | Model behaving badly |
| Assistant disable | All calls to human agents | Anything unclear |

Each is config, not a deploy — an incident response that requires a build is not
an incident response. Flipping any of them is audited.

### 8.9 Language

Indian retail banking is multilingual, and callers code-switch mid-sentence.
Language affects every layer: ASR model choice, the classifier's training
distribution, cache keys (an answer cached in English cannot serve a Hindi
caller), and TTS voice.

Language is detected once per call, carried as call state, and **included in the
cache key** alongside assurance level. **TODO:** confirm which languages a given
deployment must support before sizing any of this — it changes the ASR vendor
decision and the classifier's cost.

### 8.10 Testing

| Layer | Approach |
|---|---|
| Gateway controls | Unit tests asserting refusal: write tool rejected at registration, tool refused below `min_assurance`, entitlement denial, cross-customer key isolation at threshold 0.0 |
| Classifier contract | Fixture set with expected intents; a stub implementation for service tests |
| Agent loop | Bounded-loop tests; forced tool failures verifying degradation to handoff |
| Cache | Invalidation on account event; assurance and language namespacing |
| Regression | Recorded conversations replayed against a judge, gating deploys |

The safety controls are testable precisely because they are structural — a
refusal is assertable in a way that "the model usually doesn't" is not.

### 8.11 Continuity

Stateless orchestrator; Postgres per tenant with the bank's own backup policy.
The cache is derived and can be discarded. Loss of the orchestrator degrades to
the existing contact centre — the assistant is a deflection layer in front of a
system that already works, which is a comfortable failure position and worth
saying out loud to a risk committee.

## 9. Audit

Every turn writes an append-only record:

| Field | Why |
|---|---|
| transcript, ASR confidence | What the system believed it heard |
| intent, entities, `classifier_version` | Explains a decision against the version that made it |
| authenticated `customer_id`, assurance level, method | On whose authority, and how strongly (§4.6) |
| every tool call: name, args, result hash, latency | Which data the answer came from |
| entitlement decisions, including denials | Evidence the control ran |
| cache outcome and entry id | Whether the answer was computed or reused |
| model route, prompt hash, output | Reproducibility |
| handoff, with cause | Where the assistant stopped |

The requirement is **reconstruction**: given a complaint six months later, show
exactly what was said, on whose authority, and from which data. "The model decided
to" is not an acceptable explanation to a regulator, which is why steps 4 and 7 of
the lifecycle are deterministic and logged rather than reasoned.

Cache hits need particular care in audit: the answer served was generated at some
earlier time, so the record must reference the originating turn rather than imply
the answer was freshly computed.

## 10. Deployment

Inside the bank's perimeter, as a VPC deployment or on-prem, per their position.
One tenant per deployment (§8.4).

| Concern | Approach |
|---|---|
| Models | Self-hosted or in-region, per data residency. Changes the economics — cost becomes GPU utilisation rather than per-token |
| State | Postgres per tenant; cache is derived and rebuildable |
| Vector index | pgvector at this scale — entry counts are large and shared across replicas, unlike the main build |
| Scaling | Horizontal; the shared index makes replicas equivalent |
| Config | Tool allowlist and thresholds are versioned artefacts, deployed like code |

Note the inversion from the main assignment: there, an in-process index was
correct because entry counts were small and a single instance kept the
measurement clean. Here, shared state across replicas is a requirement, so
pgvector stops being premature and starts being necessary.

## 11. Failure modes

| Mode | Impact | Mitigation |
|---|---|---|
| Assistant performs an action | Unauthorised state change | **Impossible** — no write tool in the allowlist (§5) |
| Hallucinated payee or amount | Wrong transfer | **Impossible** — cannot initiate transfers |
| Hallucinated figure in an answer | Regulatory exposure | Template slots from tool output only (§7) |
| Cross-customer cache hit | Reportable incident | Customer in key namespace; tested at t=0 (§6.1) |
| Stale balance quoted | Wrong figure | Event invalidation; else balances are not cached (§6.2) |
| Unapproved tool invoked | Compliance breach | Static read-only allowlist (§6.1) |
| Entitlement bypass | Data exposure | Enforced in gateway, never in prompt (§6.1) |
| Agent fan-out overwhelms core banking | Outage in unrelated channels | Per-tool concurrency limits (§8.2) |
| Core banking latency spike | Dead air on a live call | Per-tool timeout, degrade to handoff (§8.3) |
| Agent loop non-termination | Cost and a silent caller | Hard bounds on depth, calls, wall clock (§3.1) |
| Misclassified state-change as read | Wrong path taken | Near-boundary intents treated as state-changing (§3.2) |
| Cached answer served below its assurance level | Authentication bypass | Assurance level is part of the cache key (§4.3) |
| Spoofed caller ID treated as identity | Account data to an impostor | ANI never reaches L2 (§4.1) |
| Answer served in the wrong language | Unusable, or wrong | Language in the cache key (§8.9) |

## 12. What would need proving before building this

1. **Context-free share of real call traffic** — determines the cache ceiling, and
   is knowable only from a bank's actual transcripts.
2. **Whether entitlement checks fit the latency budget when chained** across
   several tools in one turn.
3. **Whether compliance accepts a learned cache** with a measured false-hit bound,
   or insists on human approval per entry indefinitely.
4. **Which assurance level the bank's own policy requires** for each data class.
   This is their existing policy, not a design decision, and it caps how much
   traffic the assistant can serve without step-up.

Question 3 decides whether the architecture is viable at all, and it is not an
engineering question.

## 13. Why this is not built

Three part-time days. Building it would require a bank's systems, an entitlement
service, a core banking event stream and a compliance counterparty — none of which
exist in an evaluation exercise. Simulating all four would produce numbers that
mean nothing, and the resulting demo would be a claim rather than a measurement.

The main assignment instead measures the two mechanisms that *are* measurable,
against a real upstream on real data. This document is the argument for where they
go next, not a claim to have gone there.
