# Agentic Banking Assistant — Technical Design

**Design only. Not built.** · 2026-09-04 · [PRD](PRD.md) ·
[Main assignment](../../docs/TDD.md)

Where the main build's cache and router go with twenty backend systems, real
entitlements, and a false hit that is a reportable incident.

**Identification comes first.** For an existing customer, authentication (§2) and
authorization (§3) complete at call setup, before a single turn is processed —
which is why they precede the lifecycle (§4) here. Nothing about a customer can
be said, cached or checked until the caller is known and their entitlements are
in hand.

---

## 1. Architecture

```
        telephony / web ──► ASR (in perimeter)
                                 │
                       ┌─────────▼─────────┐
                       │   Orchestrator    │
                       └─────────┬─────────┘
   ┌──────────┬──────────┬───────┴───┬──────────┬──────────┐
classifier   cache     planner   entitlements  audit    verifier
(AI/DS dep) (scoped)   (agent)    (bank IAM)  (append)   (§8.1)
                           │
                   ┌───────▼────────┐
                   │  tool gateway  │  static, read-only allowlist
                   └───────┬────────┘
      core banking · cards · loans · CRM · KYC
```

| Component | Owns | Does not own |
|---|---|---|
| Orchestrator | Sequencing, budgets, degradation | Customer data, credentials, authorization logic |
| Classifier | Text → intent + entities | Routing, cacheability, TTL |
| Gateway | Allowlist, assurance, entitlement, budget checks | Business rules |
| Cache | Key construction, lookup, expiry | Freshness policy (tools declare it) |
| Verifier | Grounding check on generated text | Content quality |

Deployment is in-perimeter; models are self-hosted or in-region per the bank's
data residency position (§11).

## 2. Authentication — establishing who is calling

**Step one of every call.** Runs at call setup, before the first turn is
classified or answered (§4). Establishes *who is calling and how strongly that is
known*. The orchestrator holds no credential and cannot mint one; it exchanges an
opaque assertion with the bank's IAM.

### 2.1 Assurance levels

| Level | Established by | Unlocks |
|---|---|---|
| `L0` | nothing | Public: branch hours, product terms, procedures |
| `L1` | ANI match to a customer record | Generic status ("2 active cards"), no figures |
| `L2` | voice biometric, KBA, or app session token | Balances, transactions, statements |
| `L3` | OTP to registered device, in-app confirmation | State changes — which are handed off regardless (§6) |

**ANI never reaches `L2`.** Caller ID is spoofable; it narrows who the caller
claims to be and verifies nothing. Web and app channels start at `L2` by
inheriting a verified session.

### 2.2 Assurance gates data, not conversation

Insufficient assurance produces an offer, never a refusal.

```
turn requires L2, call holds L1
  → "I can get that once I verify it's you — I'll send a code to your
     registered number."
  → step-up runs in the bank's flow; call_state.assurance := L2
  → original turn resumes
```

`ToolSpec.min_assurance` (§6.1) is enforced by the gateway, so a tool returning
figures cannot be invoked at `L1` whatever the planner decided.

### 2.3 Assurance is part of the cache key

An answer computed at `L2` must never be served to an `L1` caller.

```
c:{customer_id}:{assurance}:{locale}:{classifier_version}:{intent}:{params}
```

Without `assurance` in the key, a warm cache is an authentication bypass: the
expensive path checks the level, the cheap path skips it.

### 2.4 Session binding

| Property | Rule |
|---|---|
| Scope | One call leg. Expires with it |
| Direction | Raise only; never inherited from a prior call |
| Transfer / reconnect | Re-authenticate. Assertions do not survive a channel change |
| Max lifetime | Sensitive intents re-step-up beyond `ASSERTION_TTL` |

### 2.5 Degradation

| Condition | Behaviour |
|---|---|
| IAM unavailable | `L0` only; offer handoff. Never assume identity |
| Biometric inconclusive | Hold level; offer step-up |
| Repeated step-up failure | Stop offering; handoff |
| Caller declines | Answer at current level; name what needs verification |

All paths fail toward less access.

## 3. Authorization — establishing what they may see

Authentication answers *who*. Authorization answers *may this caller see this
account*. Separate systems, separate failure modes.

**Step two of every call**, immediately after authentication. The entitlement set
is fetched once at call setup and enforced per tool call by the gateway.

**Entitlement is never derived from identity** — knowing the caller is Priya does
not say which accounts Priya may view.

### 3.1 Model

```
Entitlement := (customer_id, resource, permission)
  resource   ∈ account:{id} | card:{id} | loan:{id} | profile
  permission ∈ view_balance | view_transactions | view_statement | view_profile
```

Read permissions only — no write permission can exist because no write tool does.

| Relationship | Effect on the entitlement set |
|---|---|
| Joint, either-or-survivor | Both holders entitled to view |
| Joint, jointly operated | Both entitled to view; acting is out of scope anyway |
| Mandate holder / POA | Entitled to named accounts only, not the customer's others |
| Guardian of a minor | Entitled until a date the bank holds; expiry is not inferable here |
| Corporate signatory | Per account, often a subset |
| Frozen / dormant | May be viewable while unusable — the answer must say so |

### 3.2 Enforcement

```
gateway : entitlement(customer_id, resource, permission) → allow | deny
prompt  : nothing
```

An instruction in a prompt fails silently and leaves no evidence. A gateway
refusal fails loudly and writes an audit line. The prompt never sees an
unentitled account because the tool never returned one.

**Denials are first-class audit events**, not errors. A rising denial rate is a
signal — a bug, or probing.

### 3.3 Resolving an unqualified "my account"

```
entitled_accounts(customer) →
  1 → answer directly
  n, one designated primary → answer, naming which
  n, no primary → ask, listing masked identifiers ("savings ending 4471")
```

Never sum across accounts. A total is a number the customer did not request and
may read as a single balance.

### 3.4 Freshness

| | Rule |
|---|---|
| Fetch | Once per call, at setup |
| Within a call | Held stable in call state |
| Across calls | Never cached; a revocation takes effect on the next call |
| Accepted gap | A mandate revoked mid-call is honoured until the call ends |

The one read deliberately excluded from caching: a stale allow has unbounded
cost, a re-fetch costs one round trip already off the turn path.

## 4. Call and turn lifecycle

Identity is established once, at call setup, before any turn is processed.

```
CALL SETUP                                        ~400ms, off the turn path
 1  identify      ANI → candidate customer_id                        §2.1
 2  authenticate  IAM → assurance ∈ {L0,L1,L2,L3}                    §2
 3  authorize     entitlement service → EntitlementSet               §3
 4  disclose      recording + automation notice, consent recorded    §9.7
    ├─ call_state = {customer_id, assurance, entitlements, locale}
    └─ any step fails → L0, public information only

PER TURN
 5  transcript    ASR → text, asr_confidence
 6  classify      → intent, entities, confidence, classifier_version §5.2
 7  guard         intent ∈ STATE_CHANGING     → handoff, stop        §6
 8  guard         intent.min_assurance > held → offer step-up        §2.2
 9  cache         key(§7.1) → hit? → verify → respond                §7
10  plan          static | guided | open → tool sequence             §5.1
11  gateway       allowlist → assurance → entitlement → budget       §6.1
12  generate      base | complex, template slots from tool output    §8
13  verify        every figure traces to tool output, else fallback  §8.1
14  persist       cache store (if cacheable), audit write            §7,§10
```

**Why setup, not per turn**

| | Consequence if done per turn |
|---|---|
| Cache key contains `customer_id` and `assurance` (§7.1) | First turn of every call is uncacheable |
| IAM + entitlement fetch ≈ 400ms | ~400ms added to every answer |
| Entitlement precedes any statement about an account | Turn processed, then discarded |

`L0` is a valid terminal state, not a failure: unverified callers receive public
information (branch hours, product terms, procedures).

### 4.1 Latency budget

p95 time-to-first-audio < 1.5s. Beyond ~1.5s of silence a caller starts talking
again.

| Stage | Budget | Notes |
|---|---|---|
| ASR finalisation | ~300ms | Endpointing dominates; bought |
| Classify | ≤20ms | Contract term, §5.2 |
| Entitlement check | ≤10ms | In-memory; set fetched at call setup |
| Cache lookup | ≤10ms | Skipped on miss path only |
| Tool calls | ≤600ms | Parallel where the plan allows; long pole |
| Generation | ≤500ms base, ~2× complex | |
| Verification | ≤5ms | Deterministic scan, §8.1 |
| TTS first audio | ~200ms | Streamed |

`TODO(measure)`: per-system core banking read latency. Most likely to break the
budget, knowable only inside a given bank.

Any tool exceeding its timeout is dropped from the answer, not waited on.

## 5. Agent loop

"Agent" = the system selects **which read tools** to call, in what order. It does
not select what it is permitted to do.

### 5.1 Planning tiers

| Tier | Planner | Tool set | Example | Volume |
|---|---|---|---|---|
| `STATIC` | none | fixed sequence | "what's my balance" → `core.balance` | majority |
| `GUIDED` | base model | shortlist bound to intent | "why was my card declined" → {`cards.txn`,`cards.status`,`core.balance`} | some |
| `OPEN` | complex model | full read allowlist, depth-bounded | "explain this charge and what happens next" | tail |

```
BOUNDS = {tool_calls: 4, planning_rounds: 2, wall_clock_s: 6}
breach → handoff(reason="loop_exhausted")
```

Every planning step records the tool chosen and the justification given.

### 5.2 Classifier contract

AI/DS owns the implementation. This design owns the contract.

```python
@dataclass(frozen=True)
class Classification:
    intent: str
    entities: dict[str, str]
    confidence: float
    model_version: str          # enters the cache key (§7.1)
```

| Term | Value | Rationale |
|---|---|---|
| Latency | p99 ≤ 20ms | Sits inside the turn budget |
| Determinism | stable within a version | Cache keys must be reproducible |
| Version | changes on any behaviour change | Deploy writes a fresh namespace |

**Misclassification cost differs from the main build.** There, a wrong intent
gives an unhelpful answer; here it can mark a state-changing request as a read.

```
confidence < FLOOR_HIGH        → handoff, not a larger model
intent ∈ NEAR(STATE_CHANGING)  → treated as state-changing
```

## 6. Read-only by construction

**The allowlist contains read operations only.** The assistant cannot transfer,
block a card, raise a dispute or change a profile because no such tool exists in
the surface it can reach. Not restrained — absent.

```
"block my card"
  → classify: block_card ∈ STATE_CHANGING
  → stop. No tool call, no generation about the action.
  → handoff(reason="state_change") → app flow with step-up, IVR, or agent
  → assistant may confirm the handoff occurred; nothing further
```

| Consequence | |
|---|---|
| Hallucinated amount or payee | Impossible — no tool accepts one |
| Retried write executing twice | Impossible — §9.1 |
| Step-up authority | Belongs to the receiving flow. Voice biometrics and ASR confidence are not authority to move money |
| Cost | Card blocking becomes a handoff, not a completion. Accepted: deflection value is overwhelmingly in reads; write tail-risk is unbounded |

### 6.1 Tool gateway

Four gates, in order. Order matters: a caller with no entitlement should never
reach a budget check.

```
allowlist(deployment, tool)         enabled for this bank at all?
assurance(call.level, tool)         verified strongly enough?          §2.2
entitlement(customer, tool, args)   may THIS caller read THIS resource? §3
budget(turn)                        inside remaining time and cost?
```

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    read_only: bool          # gateway refuses registration if False
    min_assurance: str       # L0 | L1 | L2 | L3
    entitlement_scope: str
    freshness_seconds: int   # the cache asks; the tool owns staleness
    timeout_ms: int
    cache_scope: Literal["PRODUCT", "CUSTOMER", "NONE"]
```

`read_only` checked at registration is what makes §6 enforceable rather than
aspirational.

**No runtime discovery.** The allowlist is static per deployment and versioned. A
regulated deployment must be able to prove which operations were reachable during
a given call; discovery makes that unprovable.

### 6.2 MCP

Adopt the protocol and schema; discard dynamic discovery. The value is not
rewriting the adapter layer for every client — integration cost, not model cost,
dominates these deployments. Protocol standard, static allowlist.

## 7. Caching under entitlements

The main build resolves "at home" to a canonical city, making a personalised
question globally shareable. **No banking analogue exists**: a balance is
irreducibly per-customer, so the key carries the customer and the entry is never
shared.

### 7.1 Scopes and keys

| Scope | Key | Shareable | TTL | Invalidation |
|---|---|---|---|---|
| `PRODUCT` | `p:{cv}:{intent}:{params}` | all customers | 7d | content publish event |
| `CUSTOMER` | `c:{cust}:{assurance}:{locale}:{cv}:{intent}:{params}` | never | 1h | account event |
| `NONE` | — | — | — | not cached |

`cv` = classifier version. Customer identity is in the **key namespace**, not a
post-retrieval filter: a lookup can only search one customer's partition, so a
cross-customer hit requires a key-construction bug, unit-tested at similarity
threshold `0.0`.

Parameters are in the key for the same reason as the main build:
`IFSC:andheri_east` and `IFSC:andheri_west` are different requests, and
correctness must not depend on an embedding model separating them.

### 7.2 Entries store templates, not figures

```
answer_template : "Your {account_label} balance is {balance} as of {as_of}."
slots           : filled from a fresh tool call on every hit
```

A hit saves the model call without ever serving a stale number. This is what
makes customer-scoped caching viable in a domain where the data moves.

### 7.3 Invalidation

```
core_banking_events → invalidate(customer_id, account_id)
    DELETE FROM cache_entries WHERE invalidation_key = 'acct:' || account_id
```

TTL is a backstop. Where no consumable event stream exists — common —
customer-scope caching is restricted to slow-moving facts (limits, holdings, KYC
status) and balances are not cached. A deployment-time configuration, not a code
path.

### 7.4 Entries are customer records

Same retention, encryption and erasure obligations as any other customer data
store, including right-to-erasure. Not a derived convenience.

### 7.5 Staged approval

| Stage | Cache behaviour | Gate to next |
|---|---|---|
| Shadow | computes hits, serves none, logs would-be false hits | false-hit rate below bound |
| Assisted | `PRODUCT` served after human approval; seeded from the bank's existing approved-answer table | review period clean |
| Measured | `CUSTOMER` served automatically | — |

The false-hit curve from the main assignment is the evidence that moves a
deployment between stages.

## 8. Generation

| Route | Model | Use |
|---|---|---|
| `NONE` | — | Static-plan lookups answered from template |
| `BASE` | small | Formatting tool output, procedural answers |
| `COMPLEX` | large | Disputes, multi-tool reasoning, ambiguous intent |

Routing reuses the intent from §5.2 — no additional classification cost.

**Figures are never model-generated.** Balances, rates, dates and account
fragments are inserted from tool output via template slots; the model produces
only surrounding language. A model that cannot emit a figure cannot emit a wrong
one.

### 8.1 Verification before speech

Template slots close the common path; prose around them is still generated.

```
for token in numbers ∪ dates ∪ account_fragments(answer):
    if token ∉ tool_output(turn):
        discard generation → fall back to template
        record verification_violation
```

Deterministic, not a second model. Cannot catch a wrong *word*; catches the class
that matters — a figure from nowhere.

Violation rate is a monitored SLO (§14.1), not a silent fallback: a rise means a
prompt or model change degraded grounding, visible before a customer reports it.

## 9. Cross-cutting

### 9.1 Idempotency

Read-only allowlist (§6) ⇒ retried writes cannot arise on the assistant path;
reads are idempotent by nature. Idempotency remains a requirement of the
receiving flows after handoff, which are existing bank systems.

The clearest case of a constraint removing a problem rather than managing one.

### 9.2 Rate limiting

| Limit | Scope | Protects against |
|---|---|---|
| Per customer | calls, turns/min | Abuse, runaway loops |
| **Per tool** | concurrent calls per backend | Core banking sized for batch and screen traffic, not an agent fanning out several reads per turn across thousands of calls |
| Per tenant | aggregate inference spend | One bank starving another |

The per-tool limit is the one usually missed: the assistant can take down
channels it has nothing to do with. Gateway is the throttle point.

### 9.3 Timeouts, retries, circuit breaking

```
timeout   per tool, sized to §4.1, not to the backend's comfort
retry     ≤1, connection-level only, reads only
breaker   per tool; open → intents depending on it degrade to handoff
```

Which intents are blocked by which tool is **declared**, so degradation is
predictable and rehearsable rather than discovered during an incident.

### 9.4 Tenancy

Deployment boundary, not a row filter: separate instance, database, allowlist and
model credentials per bank. Cache entries cannot cross a tenant because they
never share a process.

### 9.5 Secrets, transport, storage

Credentials from the bank's vault, not application config. TLS on every hop
including in-perimeter. Transcripts and cache entries encrypted at rest. PII
redacted before any egress, including logs and telemetry.

### 9.6 Observability

Distinct from audit: different consumers, stores and retention.

| Metric | Why |
|---|---|
| Per-tool latency, error rate | Budget breaches |
| Cache hit / false-hit (shadow) | Stage gating, §7.5 |
| Routing split, escalation rate | Cost |
| Verification violations | Grounding regressions, §8.1 |
| Loop-bound breaches | Planner health |
| **Handoff rate by cause** | Leading indicator — moves before complaints |

### 9.7 Consent and disclosure

Recording and automated-processing notice on the opening turn; consent recorded
in the audit trail; refusal routes to a human rather than continuing silently.

### 9.8 Kill switches

| Switch | Effect | For |
|---|---|---|
| Cache disable | all lookups miss | poisoned or suspect entries |
| Model disable | template and static answers only, else handoff | model misbehaviour |
| Assistant disable | all calls to agents | anything unclear |

Config, not deploy. An incident response requiring a build is not an incident
response. Flipping any is audited.

### 9.9 Language

Detected once per call, carried in call state, **in the cache key** (§7.1) — an
answer cached in English cannot serve a Hindi caller. Affects ASR selection,
classifier training distribution and TTS voice.

`TODO`: confirm the language set per deployment; it drives the ASR vendor
decision.

### 9.10 Testing

| Layer | Assertions |
|---|---|
| Gateway | write tool rejected at registration; tool refused below `min_assurance`; entitlement denial; cross-customer isolation at threshold `0.0` |
| Classifier | fixture set; stub implementation for service tests |
| Agent loop | bound breaches degrade to handoff; forced tool failures |
| Cache | invalidation on account event; assurance and locale namespacing |
| Verifier | injected figure not in tool output is caught |
| Regression | recorded conversations replayed against a judge, gating deploys |

Safety controls are testable precisely because they are structural: a refusal is
assertable, "the model usually doesn't" is not.

### 9.11 Continuity

Stateless orchestrator; Postgres per tenant under the bank's backup policy; cache
is derived and discardable. Loss of the orchestrator degrades to the existing
contact centre — the assistant is a deflection layer in front of a system that
already works.

## 10. Audit

Append-only. No `UPDATE`, no `DELETE` grant.

| Field | Answers |
|---|---|
| transcript, `asr_confidence` | What the system believed it heard |
| intent, entities, `classifier_version` | Explains a decision against the version that made it |
| `customer_id`, assurance, auth method | On whose authority, and how strongly |
| entitlement decisions incl. denials | Evidence the control ran |
| tool calls: name, args, result hash, latency | Which data the answer came from |
| cache outcome + originating turn id | Whether the answer was computed or reused |
| model route, prompt hash, response | Reproducibility |
| verification outcome | §8.1 |
| handoff reason | Where the assistant stopped |

Requirement is **reconstruction**: given a complaint six months later, show what
was said, on whose authority, from which data. "The model decided to" is not an
acceptable explanation, which is why steps 7 and 11 of §4 are deterministic.

A cache hit references the originating turn rather than implying fresh
computation.

## 11. Interfaces, state and schema

### 11.1 Service surface

Driven by the channel adapter, not the customer.

```jsonc
POST /call/start
  → {"channel": "telephony|web", "ani": "...", "session_token": "...", "locale": "en-IN"}
  ← {"call_id": "...", "assurance": "L2", "entitled": ["account:1", "card:9"]}

POST /turn
  → {"call_id": "...", "turn_id": "...", "transcript": "...", "asr_confidence": 0.94}
  ← {"speech": "...", "intent": "balance", "assurance": "L2",
     "tools": ["core.balance"], "cached": false,
     "handoff": null, "trace_id": "..."}

POST /call/end     flush state, close the audit record
GET  /health       per-dependency: IAM, entitlements, core banking, models, cache
POST /admin/kill   §9.8; audited, config-driven
```

### 11.2 State ownership

| State | Lifetime | Location |
|---|---|---|
| Assertion, assurance | one call | call state (memory) |
| Entitlement set | one call (§3.4) | call state |
| Conversation context | one call | call state |
| Cache entries | TTL or event (§7.3) | shared store |
| Audit records | regulatory retention | append-only store |
| Customer master data | — | bank's systems; **not held here** |

Call state dies with the call. Resuming an assertion across a reconnect is how a
call gets hijacked.

Holding no customer master data keeps erasure tractable: a request touches the
audit store and the cache, not a private copy.

### 11.3 Schema

```sql
CREATE TABLE cache_entries (              -- per tenant
  id                 BIGSERIAL PRIMARY KEY,
  scope              TEXT NOT NULL,       -- PRODUCT | CUSTOMER
  scope_key          TEXT NOT NULL,       -- §7.1
  invalidation_key   TEXT,                -- acct:{id}
  question           TEXT NOT NULL,
  answer_template    TEXT NOT NULL,       -- slots, not figures (§7.2)
  embedding          BYTEA NOT NULL,
  embedding_version  TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  approved_by        TEXT,                -- assisted stage (§7.5)
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON cache_entries (scope_key, expires_at);
CREATE INDEX ON cache_entries (invalidation_key);

CREATE TABLE audit_turns (                -- append-only
  id                    BIGSERIAL PRIMARY KEY,
  call_id               TEXT NOT NULL,
  turn_id               TEXT NOT NULL,
  customer_id           TEXT,
  assurance             TEXT NOT NULL,
  auth_method           TEXT,
  intent                TEXT,
  classifier_version    TEXT,
  entitlement_decisions JSONB NOT NULL,   -- including denials
  tool_calls            JSONB NOT NULL,   -- name, args, result hash, latency
  cache_entry_id        BIGINT,           -- set on hit; origin turn
  model_route           TEXT,
  prompt_hash           TEXT,
  response_text         TEXT,
  verification          JSONB,            -- §8.1
  handoff_reason        TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 12. Handoff

A designed outcome, not an error path.

```
handoff = {reason, call_id, transcript, intent, customer_id,
           assurance, tools_called, context_id}
```

| Reason | Trigger |
|---|---|
| `state_change` | intent ∈ STATE_CHANGING (§6) |
| `insufficient_assurance` | step-up declined or failed (§2.5) |
| `low_confidence` | below `FLOOR_HIGH` (§5.2) |
| `tool_unavailable` | breaker open (§9.3) |
| `loop_exhausted` | bounds breached (§5.1) |
| `customer_request` | asked for a person |
| `distress` | conduct signal in transcript (§13) |

Enumerated, not free text, because handoff rate by cause is the leading
indicator (§9.6).

**Authentication does not transfer as a claim.** The agent's system re-derives
assurance from IAM rather than trusting a field the assistant sent — otherwise
the assistant is an authentication bypass with extra steps.

Context transfers so the customer does not repeat themselves. No agent available
→ say so, offer a callback; do not resume attempting a question already ruled out.

## 13. Regulatory posture

Not legal advice; every row needs the bank's compliance function to confirm.

| Requirement | Where it lands |
|---|---|
| Data localisation (RBI) | Models in-perimeter or in-region; no customer data leaves (§1) |
| Consent to record and automate | Opening turn, recorded, refusal → human (§9.7) |
| Right to erasure | Audit store and cache only (§11.2, §7.4) |
| Auditability of automated decisions | Full reconstruction incl. denials (§10) |
| No unlicensed advice | Advice answered from product data or handed off |
| Fair treatment / accessibility | Human handoff always available (§12) |
| Vulnerable customers | Distress signals route to a human |

The last row is a conduct risk, not an experience gap: an assistant that
cheerfully quotes a balance to someone in evident distress.

## 14. Service levels, capacity, rollout

### 14.1 Targets

| | Target |
|---|---|
| p95 time to first audio | < 1.5s |
| Availability, contact-centre hours | 99.9% |
| Cross-customer exposure | 0, provable by construction (§7.1) |
| Containment (no handoff) | ≥ 60% of eligible calls |
| Verification violations | < 0.1% of generated turns |

### 14.2 Capacity

Concurrency is measured in simultaneous calls. A turn is I/O-bound across several
backends, so the ceiling is upstream capacity and per-tool limits (§9.2), not
orchestrator CPU.

**Core banking is the binding constraint** and is not sized for this traffic
shape. `TODO(measure)`: per-system read latency and safe concurrency before
committing to a containment target.

### 14.3 Rollout

Each stage reversible by config (§9.8), not by deploy.

| Stage | Scope | Cache mode |
|---|---|---|
| 1 | Bank staff only, all intents | Shadow |
| 2 | Branch hours + product info, traffic slice | Assisted |
| 3 | Read-only account intents at `L2` | Assisted |
| 4 | Full read surface | Measured |

State-changing intents never enter this sequence. They remain handoffs absent a
separate mandate with step-up and a confirmation protocol.

## 15. Failure modes

| Mode | Impact | Mitigation |
|---|---|---|
| Assistant performs an action | Unauthorised state change | **Impossible** — no write tool (§6) |
| Hallucinated payee or amount | Wrong transfer | **Impossible** — cannot initiate |
| Figure not in tool output | Wrong number spoken | Verifier discards generation (§8.1) |
| Cross-customer cache hit | Reportable incident | Customer in key namespace; tested at `t=0` (§7.1) |
| Answer served below its assurance | Authentication bypass | Assurance in the key (§2.3) |
| Spoofed ANI treated as identity | Data to an impostor | ANI never reaches `L2` (§2.1) |
| Entitlement inferred from identity | Data exposure | Entitlement fetched, never derived (§3) |
| Revoked mandate honoured | Access after withdrawal | Never cached across calls (§3.4) |
| Stale balance quoted | Wrong figure | Templates + event invalidation (§7.2, §7.3) |
| Entitlement bypass | Data exposure | Gateway, never prompt (§3.2) |
| Unapproved tool invoked | Compliance breach | Static read-only allowlist (§6.1) |
| Agent fan-out floods core banking | Outage in unrelated channels | Per-tool concurrency (§9.2) |
| Core banking latency spike | Dead air | Per-tool timeout → handoff (§9.3) |
| Loop non-termination | Cost, silent caller | Hard bounds (§5.1) |
| State change classified as read | Wrong path | Near-boundary intents treated as state-changing (§5.2) |
| Wrong language served | Unusable | Locale in the key (§9.9) |

## 16. Open questions

1. **Context-free share of real call traffic** — caps the cache ceiling. Knowable
   only from a bank's transcripts.
2. **Chained entitlement checks within the latency budget** — several tools in
   one turn.
3. **Whether compliance accepts a measured false-hit bound**, or requires human
   approval per entry indefinitely. Decides whether §7.5 stage 4 is reachable.
4. **Core banking event stream availability** — determines whether balances are
   cacheable at all (§7.3).

Question 3 is not an engineering question and decides whether the architecture is
viable.

## 17. Why this is not built

Requires a bank's systems, an entitlement service, a core banking event stream
and a compliance counterparty. Simulating all four produces numbers that mean
nothing.

The main assignment measures the two mechanisms that *are* measurable against a
real upstream. This is the argument for where they go next.
