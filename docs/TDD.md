# Sarjy — Technical Design

| |                                                                                     |
|---|-------------------------------------------------------------------------------------|
| Owner | aseedusmani@gmail.com                                                               |
| Updated | 2026-09-01                                                                          |
| Related | [PRD](PRD.md) · [Bonus: agentic banking assistant](../bonus_assignment/docs/TDD.md) |

## 1. Architecture

One FastAPI process on Render serving static HTML from the same origin — no
separate frontend deploy, no CORS. Postgres (Neon) for durable state. Groq for
inference. Vector search in-process. Voice is the browser's Web Speech API, so
there is no audio infrastructure.

```
POST /chat {session_id, text}
  [1] embed(text)              → 384-dim vector        ~8ms, not an LLM
  [2] classify(v)              → intent                ~1ms, same vector
  [3] extract entities         → city, day, unit       <1ms, gazetteer
  [4] resolve "home" from facts→ canonical city
  [5] cache lookup on key      → HIT? return           ~1ms, no model
  [6] Open-Meteo if needed                             ~140ms
  [7] route → SMALL | LARGE → generate                 300–700ms
  [8] store entry + write request_log
```

Steps 1–5 are the lookup path: ~10ms, no generation.

## 2. Cost classes

The design rests on one asymmetry: interpreting a question is cheap, answering it
is not. An embedding emits a vector, not tokens; one embedding serves both
classification and cache lookup.

| | model | params | $/turn | latency |
|---|---|---|---|---|
| Embedding | MiniLM-L6-v2, ONNX int8 | 22.7M | ~0, local CPU | TODO |
| Small | llama-3.1-8b-instant | 8B | TODO | TODO |
| Large | llama-3.3-70b-versatile | 70B | TODO | TODO |

**TODO(pricing):** look up current Groq per-token rates and cite with the date
retrieved. Confirm both model IDs are still served.

**TODO(measure):** prompt composition. Log `tokens_in` / `tokens_out` on the day-1
baseline run and record the actual split across system prompt, facts, history,
tool output and query. Input tokens dominate cost, so this figure scales
everything downstream.

**What is safe to assert before measuring:** a cache hit consumes zero tokens,
and the small model is materially cheaper than the large one. The size of that
gap is the thing to verify, not assume.

## 3. Intents (~25)

| Group | Cacheable | Route |
|---|---|---|
| Lookup — current, temperature, rain, wind, humidity, sunrise, AQI | yes | SMALL |
| Forecast — today, tomorrow, n-days | yes | SMALL |
| Compare cities | yes | SMALL |
| Advice — clothing, travel, activity | no | LARGE |
| Memory — set home, set units, recall | no | SMALL |
| Contextual follow-ups | no | LARGE |
| Chat — greeting, thanks, out-of-scope | yes | SMALL |

Classification is kNN against a labelled intent index. No model call.

Advice is uncacheable: the answer depends on live conditions, so the tool result
changes underneath an identical question.

## 4. Cache

### 4.1 Entities go in the key, not the vector

`"weather in Delhi"` vs `"weather in Mumbai"`: cosine ≈**0.96**. Same question
shape, different answers. **No threshold separates them** — they really are
semantically near-identical. Embeddings capture topic well and specifics badly.

So specifics move into the key:

```
key = f"{intent}|{sorted(params)}"

"weather in Delhi"     → current_weather|city=delhi
"how's Delhi looking"  → current_weather|city=delhi     ← same key, can hit
"weather in Mumbai"    → current_weather|city=mumbai    ← separate namespace
```

Vector similarity then only resolves *phrasing* within a fixed parameter set,
which is what it's good at. Entity collision is prevented structurally.

**This is the central design finding.** A vector-only cache is unsafe here, and
tuning cannot fix it.

### 4.2 Personalisation resolves to canonical params

"Weather at home" is not a per-user entry. Home city is resolved from `facts`
*before* the key is built:

```
"weather at home" + facts{home_city: Bengaluru} → current_weather|city=bengaluru
```

Shareable with every user asking about Bengaluru — per-user misses become global
hits.

This does **not** transfer to banking: a balance is irreducibly per-customer, so
the key must carry the customer and entries can never be shared. See the bonus
TDD.

### 4.3 Freshness — owned by the tool

| Group | TTL | Why |
|---|---|---|
| Current conditions | 10 min | Open-Meteo refreshes ~15 min |
| Forecast | 60 min | Model runs hourly |
| Chat | 24 h | Static |

`weather.freshness_seconds` is declared next to the fetcher; the cache asks. The
banking equivalent is event-driven invalidation, which needs the same interface.

### 4.4 Storage

In-process numpy, persisted to Postgres.

```python
def search(self, v, key):
    idx = self.by_key.get(key)               # exact key filter FIRST
    if not idx: return None
    sims = self.vecs[idx] @ v                # normalised → dot == cosine
    best = int(np.argmax(sims))
    return idx[best] if sims[best] >= THRESHOLD else None
```

```sql
CREATE TABLE cache_entries (
  id BIGSERIAL PRIMARY KEY,
  cache_key  TEXT NOT NULL,
  question   TEXT NOT NULL,
  answer     TEXT NOT NULL,
  embedding  BYTEA NOT NULL,           -- float32[384]
  hits       INT NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON cache_entries (cache_key, expires_at);
```

At n<2,000 a numpy scan is <1ms — faster than a network hop. pgvector + HNSW is
the production path, stated not built.

### 4.5 Limits

Contextual turns ("what about tomorrow?") carry no context in their embedding, so
they bypass the cache. Hit rate is therefore reported **as a fraction of
context-free traffic**, with that share measured separately. Reporting against
total traffic would overstate the ceiling.

## 5. Router

Reuses the intent from §3 — no extra classification cost.

No escalation path. An escalated turn pays for both models, so it is only
cost-negative when escalation is very frequent:

```
break-even:  small_cost + e · large_cost = large_cost
             e = 1 − small_cost/large_cost
```

Given a large price gap between tiers, `e` is high — meaning escalation is
bounded by latency and classifier health, not by cost. **TODO(compute):** the
actual break-even once rates are confirmed. Dropped for build budget regardless;
first thing to add back.

## 6. Cost model

**Not yet computed. Every figure below is a measurement to take, not a result.**

Cost per turn by path:

| Path | $/turn | $/1,000 |
|---|---|---|
| LARGE (baseline) | TODO | TODO |
| SMALL | TODO | TODO |
| Cache hit | 0 | 0 |

**Scope:** this models *LLM cost only*. Hosting, Postgres and the embedding
model's CPU time are excluded. They are fixed rather than per-turn, but the
headline figure should be labelled "LLM cost per turn" and not "cost per turn".

### Method

1. Day 1 — run the baseline (`MODE=baseline`) over the workload. Record measured
   `tokens_in`/`tokens_out` per turn and compute baseline cost from published
   rates.
2. Day 2 — run `MODE=router`, then `MODE=full`. Record the routing split, the
   context-free share and the cache hit rate as **measured** values.
3. Compute the reduction from the measured numbers only.

```
cost_per_turn = (1 − hit_rate) · (small_share · small_cost + large_share · large_cost)
```

**TODO(measure):** `hit_rate`, `small_share`, `large_share`, `context_free_share`.
All four were assumed in an earlier draft and are now deliberately unset — the
whole point of the evaluation is to produce them.

### Why the result is expected to hold anyway

The cache hit rate is the most uncertain term, and it is also the term the goal
least depends on. Routing alone — sending lookups to the small model and
reasoning to the large one — captures most of the available saving, because the
price gap between model tiers is large and most traffic is lookups. The cache is
upside on top.

**TODO(validate):** once rates are confirmed, compute the routing-only reduction
and check it against G1. If routing alone clears the goal, the cache is
de-risked; if it does not, G1 needs resetting against measured data.

## 7. Evaluation

### 7.1 Workload generator — built first

≥400 turns with ground truth; nothing in PRD §6 is computable without it.

- Zipf(α≈1.1) over 25 intents, city distribution skewed to metros
- 3–6 paraphrases per (intent, entity), including code-switched Hinglish
- Mix: 55% lookup/forecast, 15% advice, 15% contextual, 10% memory, 5% chat
- **Adversarial pairs:** one entity differing — Delhi/Mumbai, today/tomorrow, °C/°F
- Each turn labelled `(intent, params, expected_answer_key)`

A **false hit** is a returned entry whose `expected_answer_key` differs from the
query's. Hand-check 30 labels before trusting any safety number.

### 7.2 Threshold sweep

`t` ∈ [0.70, 0.99] step 0.01. Record hit rate and false-hit rate. Ship the lowest
`t` holding false-hit <1%.

### 7.3 Gates

| Test | Assertion |
|---|---|
| Entity isolation | `city=mumbai` never returns a `city=delhi` entry, **at threshold 0.0** |
| Advice non-caching | No entry written for advice or contextual intents |
| Freshness | After `expires_at`, identical query MISSes |
| Baseline parity | `MODE=baseline` bypasses cache and router |

Threshold-0.0 is the important one: it proves entity separation is structural,
not tuned.

## 8. Failure modes

| Mode | Mitigation | Residual |
|---|---|---|
| Entity collision | Entity in key; tested at t=0 | Gazetteer miss |
| Gazetteer miss | **Fail closed** — mark uncacheable rather than cache on a partial key | — |
| Stale data | Tool-declared TTL | No event invalidation |
| Cache poisoning | TTL + flush | **Unmitigated — no answer validation** |
| Embedding OOM on 512MB | int8 ONNX ~23MB; validated day 1 | — |

## 9. Alternatives considered

| Alternative | Rejected because |
|---|---|
| Exact-match cache | ASR surface variance drives hit rate to ~0 |
| Vector-only key | Delhi/Mumbai cosine ≈0.96 — unseparable. The central finding |
| LLM-based classification | A model call to avoid a model call inverts the economics |
| Provider prompt caching only | Complementary — the system prompt is a fixed, repeated prefix — but it discounts input tokens only and does not eliminate generation. Layer it, don't substitute. TODO: measure the prefix share of total input |
| pgvector + HNSW | Right at scale; needless network hop at n<2k |
| MCP tool layer | One tool doesn't justify a protocol. See bonus TDD |

## 10. Rollout

`MODE ∈ {baseline, router, full}`, runtime-selectable. Day 1 ships `baseline` to
get measured reference numbers on the deployed URL. Day 2 enables `router`, then
`full`. All three stay selectable so every comparison is same-code.
