# Sarjy — Technical Design

Owner: aseedusmani@gamorite.com · Updated 2026-09-01 · [PRD](PRD.md) ·
[Bonus design](../bonus_assignment/docs/TDD.md)

> Written to be explicit enough for AI-assisted implementation. Unknowns are
> marked `TODO(...)` rather than filled with plausible placeholders.

## 1. Framing

**MVP scope.** A web service with an expensive upstream — an LLM API, slow and
metered, on the request path — carrying traffic that is repetitive and mostly
easy. So: cache it, route cheap work to a cheap tier, measure both.

The non-trivial part is the cache key: callers never phrase a request the same
way twice, so it can't be the request text.

Single instance, no auth, one tool, Chrome only. Production concerns are designed
in `bonus_assignment/`, not built here.

## 2. Architecture

One Python service, one container, static client served from the same origin.

```
browser ──► FastAPI ──► in-process vector index (~3MB)
                    ├─► Postgres (Neon)
                    └─► Groq · Open-Meteo
```

| Component | Responsibility |
|---|---|
| Pipeline | Orders the stages of a request; timeouts and fallbacks |
| Classifier | External dependency behind an interface (§4) |
| Cache | Key construction, lookup, expiry |
| Router | Picks the model tier on a miss |
| Log | One row per request; the source of every published number |

## 3. API

No auth — public demo. `session_id` is a client-generated string, not an identity.

```jsonc
POST /chat
  → {"session_id": "a7f3c21e", "text": "weather in Delhi"}   // text ≤ 500 chars
  ← {"answer": "...", "intent": "current_weather", "params": {"city": "delhi"},
     "cached": true, "route": null, "latency_ms": 12, "trace_id": "01J8X4"}

GET  /health   → {"ok": true, "mode": "full", "cache_entries": 412}
GET  /metrics  → counters from request_log: hit rate, routes, latency, tokens
POST /admin/flush → {"evicted": 412}
```

`cached: true` implies `route: null`. Errors return
`{"error": {"code", "message", "trace_id"}}` — `invalid_request` (400),
`upstream_timeout` (503), `tool_unavailable` (503). **No error path invents an
answer.**

`X-Sarjy-Mode: baseline | router | full` overrides the default per request, so
A/B runs need no redeploy.

## 4. The classifier is a dependency

Understanding a request is an AI/DS concern. This service depends on a contract,
not an implementation. A rule-based version ships as a stand-in.

```python
@dataclass(frozen=True)
class Classification:
    intent: str
    params: dict[str, str]      # {"city": "delhi"}
    confidence: float
    model_version: str
```

Contract terms the backend owns: p99 ≤ 20ms (it sits inside the fast path),
deterministic within a version (keys must be stable), and a version that changes
on any behaviour change.

**Degradation.** Classifier down, confidence below floor, or params unresolved →
treat as uncacheable and route to the large tier. Always fail toward *expensive
but correct*, never toward a guessed cache namespace.

**Versioning — the backend's problem.** `classifier_version` is part of the cache
key, so a deploy writes into a fresh namespace instead of serving entries under
semantics that no longer hold. Stored vectors are only comparable within one
embedding model, so `embedding_version` mismatch is a miss, not a comparison.

## 5. Intent registry

The classifier maps text to an intent name (§4). **What the system does with that
name is backend configuration**, declared here and owned by this service. The
classifier does not decide routing, cacheability or TTL; it decides only which
label applies.

```python
@dataclass(frozen=True)
class IntentSpec:
    name: str
    params: tuple[str, ...]        # required for a valid cache key
    cacheable: bool
    ttl_group: str                 # current | forecast | static
    tier: Literal["small", "large"]
    needs_tool: bool
```

| Intent | Params | Cache | TTL | Tier |
|---|---|---|---|---|
| `current_weather` | city | ✓ | current | small |
| `temperature` | city | ✓ | current | small |
| `rain_now` | city | ✓ | current | small |
| `wind` | city | ✓ | current | small |
| `humidity` | city | ✓ | current | small |
| `sunrise_sunset` | city | ✓ | current | small |
| `air_quality` | city | ✓ | current | small |
| `forecast_today` | city | ✓ | forecast | small |
| `forecast_tomorrow` | city | ✓ | forecast | small |
| `forecast_days` | city, days | ✓ | forecast | small |
| `rain_forecast` | city, day | ✓ | forecast | small |
| `temp_range` | city, day | ✓ | forecast | small |
| `compare_cities` | city_a, city_b | ✓ | current | small |
| `clothing_advice` | city | ✗ | — | large |
| `travel_advice` | city, day | ✗ | — | large |
| `activity_advice` | city, activity | ✗ | — | large |
| `set_home_city` | city | ✗ | — | small |
| `set_units` | unit | ✗ | — | small |
| `recall_fact` | key | ✗ | — | small |
| `weather_at_home` | — | ✓* | current | small |
| `follow_up` | — | ✗ | — | large |
| `greeting` | — | ✓ | static | small |
| `thanks` | — | ✓ | static | small |
| `out_of_scope` | — | ✓ | static | small |
| `unknown` | — | ✗ | — | large |

`weather_at_home` resolves to `current_weather|city=<stored>` before key
construction, so it shares entries with everyone else asking about that city.

**Advice intents are uncacheable** because the answer depends on live conditions:
the tool result changes underneath an identical question, so a cached reply would
be confidently out of date.

**A request whose required params don't resolve is uncacheable**, never cached
under a partial key (§4.2).

## 6. Request pipeline

```
1 classify        → intent, params, confidence, version    external (§4)
2 gate            → low confidence / unresolved → uncacheable
3 resolve         → "home" → stored city
4 key             → f"{version}|{intent}|{params}"
5 cache lookup    → hit? return                            local, ~1ms
6 tool fetch      → Open-Meteo (declares its own freshness)
7 route + call    → small | large
8 write entry, log row, respond
```

Steps 1–5 are local: no network egress.

### 6.1 Latency budget

G4 targets a 25% p50 reduction, so the budget is allocated rather than hoped for.

| Stage | Budget | Owner |
|---|---|---|
| Classify | ≤20ms | Contract term (§4) |
| Cache lookup | ≤5ms | Ours — in-process scan |
| Tool fetch | ≤200ms | Open-Meteo; skipped on a cache hit, and geocoding is memoised |
| Model call | TODO(measure) | Upstream; observed, not controlled |
| Serialise + transport | ≤20ms | Ours |

**Where the reduction comes from.** A cache hit skips the tool call *and* the
model call, so it costs roughly the local path alone — the difference between a
hit and a miss is most of the request. Routing contributes separately, since the
small tier returns faster than the large one.

Both effects are mix-dependent: p50 improves only if enough traffic hits. That is
the same uncertainty as G1 and is measured, not assumed.

**TODO(measure):** per-stage p50 and p95 from `request_log` on the day-1 baseline,
and again in full mode. Upstream model latency is an observed property of a
dependency — recorded, not budgeted.

## 7. Frontend

One HTML file, vanilla JS, no build step. Browser `SpeechRecognition` in,
`speechSynthesis` out. A text input stays as a permanent fallback so a microphone
failure never blocks a demo. A small panel shows intent, cache hit/miss, route
and latency — free, since `/chat` already returns them.

## 8. Memory

The brief requires facts to survive across sessions. This is a write path and a
read path — no inference on our side.

**Writing.** The classifier returns `set_home_city` or `set_units` with the
parameter already extracted. The backend upserts:

```
intent=set_home_city, params={city: "delhi"}
  → UPSERT facts (session_id, 'home_city', 'delhi')
  → confirm: "Got it, I'll remember Delhi."
```

Recognising that an utterance states a preference is the classifier's job.
Persisting it is ours. There is no separate extraction step and no model call on
the write path.

**Reading.** Facts are loaded once per request into a dict, used for two things:

1. **Parameter resolution** — `weather_at_home` becomes `city=delhi` before the
   cache key is built, so personalisation resolves to a shareable entry (§5).
2. **Prompt context** — units and home city are injected so answers respect them.

**Conflict.** Last write wins, by primary key. A stored fact is a current
preference, not a history. Superseding versions with validity intervals is a real
design, and out of scope here — noted in `bonus_assignment/`.

**Scope.** `session_id` comes from browser `localStorage`. Clearing site data
loses the facts. There are no accounts (§3), so this is memory-per-browser, not
memory-per-person, and the demo says so rather than implying otherwise.

## 9. Storage

```sql
CREATE TABLE facts (                    -- cross-session memory
  session_id TEXT, key TEXT, value TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, key));

CREATE TABLE cache_entries (            -- derived; safe to truncate
  id BIGSERIAL PRIMARY KEY,
  cache_key         TEXT NOT NULL,      -- "{classifier_version}|{intent}|{params}"
  question          TEXT NOT NULL,
  answer            TEXT NOT NULL,
  embedding         BYTEA NOT NULL,     -- float32[384], normalised
  embedding_version TEXT NOT NULL,
  hits              INT  NOT NULL DEFAULT 0,
  expires_at        TIMESTAMPTZ NOT NULL);
CREATE INDEX ON cache_entries (cache_key, expires_at);

CREATE TABLE request_log (              -- every published number comes from here
  id BIGSERIAL PRIMARY KEY, trace_id TEXT, mode TEXT NOT NULL,
  intent TEXT, confidence REAL,
  cacheable BOOLEAN NOT NULL, cached BOOLEAN NOT NULL, similarity REAL,
  route TEXT, tokens_in INT, tokens_out INT, latency_ms INT, error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
```

`similarity` is logged so the threshold sweep replays from the log instead of
re-running the workload. `cacheable` separates "chose not to cache" from "missed".

Schema applied at boot with `CREATE TABLE IF NOT EXISTS`. No migration tool —
stated as a gap.

## 10. The cache

### 10.1 Key construction — the core problem

A cache key must encode the **identity** of a request, not its surface form.

Two requests with different parameters have different answers by definition —
`city=delhi` and `city=mumbai` are not the same request however similar their
wording. Keying on text similarity alone makes that distinction depend on a
tunable threshold and on an embedding model owned by another team, which can be
retrained or replaced without notice. Cache correctness must not be contingent on
either.

So identifying parameters go in the key, and similarity is used only for the job
it suits: matching different phrasings of the *same* request.

```
"weather in Delhi"    → v3|current_weather|city=delhi
"how's Delhi looking" → v3|current_weather|city=delhi   ← same key, can hit
"weather in Mumbai"   → v3|current_weather|city=mumbai  ← separate namespace
```

Exact key match first, similarity second, and only within one namespace. Requests
with different parameters cannot collide regardless of how the embedding model
behaves.

This is a cache-design decision, not a model one. It is stated here because the
obvious implementation — embed the text, search everything, threshold the score —
is unsafe in a way that is not visible until it returns the wrong city.

### 10.2 Lookup and expiry

```python
idx = by_key.get(key)                    # namespace filter first
sims = vecs[idx] @ vec                   # normalised → dot == cosine
return idx[argmax] if max(sims) >= THRESHOLD else None
```

**The table is not the cache.** The lookup is the in-process numpy index; Postgres
is durability, read once at boot and written through on update. It is never on the
request path.

At n<2k a scan is sub-millisecond and **exact**, which matters because an
approximate index would confound a false-hit measurement.

In production the index must move out of the process — two replicas would warm
independently — at which point pgvector or Redis holds it and the in-process array
becomes an L1 in front of it. Additive, not a rewrite.

Freshness is declared by the tool that owns the data (current conditions 10 min,
forecast 60 min), not by the cache.

Follow-ups ("what about tomorrow?") carry no context in their text, so they
bypass the cache. Hit rate is therefore reported **against context-free traffic**,
with that share measured separately.

## 11. Routing

| Intents | Tier |
|---|---|
| Lookup, forecast, compare, memory, chat | small |
| Advice, contextual follow-ups | large |

The classifier already produced the intent, so routing costs nothing extra. No
escalation path in this build — an escalated request pays both tiers and is only
net-negative when escalation is frequent (`e = 1 − small/large`).
**TODO(compute)** once rates are confirmed.

## 12. Measurement

Every published number comes from `request_log`.

**Workload** (built first — nothing is measurable without it): ≥400 requests over
~25 intents, Zipf-distributed, 3–6 phrasings each, plus adversarial pairs
differing in one parameter (Delhi/Mumbai, today/tomorrow). Each labelled
`(intent, params, expected_answer_key)`. A **false hit** is a returned entry whose
`expected_answer_key` differs. Hand-check 30 labels first.

**Tool responses are recorded and replayed.** Baseline and treatment runs happen
at different times, and the weather changes between them. Comparing answers across
runs would then be measuring the atmosphere as much as the pipeline.

So the baseline run writes every Open-Meteo response to a fixture file keyed by
`(intent, params)`, and later runs read from the fixture instead of the live API.
The upstream is pinned; only our code varies. The live path stays in use for the
demo — this applies to evaluation runs only, via `TOOL_SOURCE=fixture|live`.

Without this, G2 is not measurable.

**Threshold sweep:** replay at `t` ∈ [0.70, 0.99]; plot hit rate and false-hit
rate; ship the lowest `t` holding false-hit <1%. Replayed from `request_log`
similarities, so a sweep costs no upstream calls.

| Gate | Assertion |
|---|---|
| Namespace isolation | `city=mumbai` never returns a `city=delhi` entry, **at threshold 0.0** |
| Non-caching | No entry written for advice or contextual intents |
| Mode parity | `mode=baseline` bypasses cache and router |

Threshold-0.0 matters most: it proves separation is structural, not tuned.

**Quality:** the upstream is non-deterministic, so 50 sampled responses are scored
against baseline. Regression testing for a non-deterministic dependency.

**TODO(pricing):** current Groq rates, cited with a date. **TODO(measure):**
prompt size, hit rate, routing split, context-free share. All were assumed in an
earlier draft and are deliberately unset.

## 13. Deployment

One container on Render, Postgres on Neon, regions matched and close to where the
demo is given. Config is environment variables — API keys, `MODE`, `SIMILARITY_THRESHOLD`,
`CONFIDENCE_FLOOR`, `TOOL_SOURCE`, model ids — so the sweep's output applies
without a code change. Embedding model baked in at build time, not
fetched at boot.

**Single instance, deliberately.** Two replicas would hold two independent
caches: hit rate halves and the same question resolves differently depending on
which one answers. For a service whose output is a measured hit rate, that makes
the measurement meaningless. Scaling out requires a shared index first.

Free tier sleeps after ~15 min and takes 30–60s to wake, so keep-warm ping
`/health`, and open the URL an hour before the presentation.

**Deploy an empty service on day 1**, before any features, so deployment problems
and application bugs never arrive together.

## 14. Alternatives considered

| Alternative | Rejected because |
|---|---|
| Exact-match cache on text | Speech input varies constantly; hit rate ≈0 |
| Similarity-only key | Makes correctness depend on a threshold and on a model owned by another team. Different parameters must not be separable by tuning — they must be unable to collide |
| LLM call to classify | Paying the expensive dependency to decide whether to skip it |
| Provider prompt caching alone | Complementary — discounts repeated input, doesn't remove the call |
| pgvector + HNSW | Right at scale. Here it adds a network hop that dwarfs a 0.2ms scan, and is approximate where the measurement needs exact |
| Owning the classifier | An AI/DS artefact. This service owns the contract, not the model |
| Separate frontend deploy | Two artefacts, CORS, and a build toolchain for one HTML file |
