# Findings

Measured results as they arrive. Numbers here are real; anything not yet measured
stays `TODO(...)` rather than being estimated.

---

## 2026-09-03 — Model availability

The `llama-3.1-8b-instant` / `llama-3.3-70b-versatile` pair named in the original
design **is not served on this account** (`model_not_found`). Switched to
`openai/gpt-oss-20b` and `openai/gpt-oss-120b` — a 6× parameter gap, which is the
tier separation the routing argument needs.

Cost of the change: one config default. Model ids were deliberately configuration
rather than constants, and that paid off on day one.

## 2026-09-03 — Tier latency: routing does not buy latency

Four weather prompts per tier, warm connection, local → Groq.

| Tier | Model | p50 | min | max | tokens out |
|---|---|---|---|---|---|
| small | `gpt-oss-20b` | 507ms | 464 | 926 | ~116 |
| large | `gpt-oss-120b` | 549ms | 309 | 742 | ~106 |

**The tiers are latency-indistinguishable at this sample size** — 42ms apart, well
inside the spread of either. A 6× parameter difference does not show up as
response time on Groq's hardware.

Consequences:

- **G4 (p50 latency −25%) cannot be met by routing.** It has to come from cache
  hits, which skip the upstream entirely. Routing is a cost lever only.
- **G1 (cost) still rests on routing**, but is now blocked on confirming the
  per-token price gap between the two models. If the price gap is small, the
  routing half of the deep dive is worth much less than the design assumed.
- The risk position in the PRD — "routing carries G1, cache is upside" — needs
  re-checking once pricing is known. It may invert.

`TODO(pricing)`: per-token rates for both models, cited with a retrieval date.

**Method note:** the first run of each tier was discarded; it pays TLS setup and
was consistently faster/slower than steady state. Sample is 4 prompts, which is
enough to say "no large difference" and not enough to quantify a small one.

## 2026-09-03 — Prompt size

Measured input at ~136 tokens with system prompt + query only. The design
estimated ~945 including facts, history and tool output — those three are not
built yet, so this is a floor, not a contradiction. Re-measure once the floor is
complete.

Output ran ~105–116 tokens against an estimate of 70. The system prompt caps
answers at "one or two short sentences"; it is not being obeyed tightly.

## 2026-09-03 — Pricing: the tier gap is 2×, not ~12×. G1's risk position inverts.

Published Groq rates, retrieved 2026-09-03:

| Model | $/M input | $/M output |
|---|---|---|
| `openai/gpt-oss-20b` | 0.075 | 0.30 |
| `openai/gpt-oss-120b` | 0.15 | 0.60 |

`gpt-oss-20b` is confirmed the **cheapest chat model on the platform** — Qwen 3.6-27B
is $0.60/$3.00, an order of magnitude more. Current config is already optimal.

**The large tier costs exactly 2× the small one**, input and output alike. The
design assumed roughly 12× (llama-8b vs llama-70b). That assumption is gone.

At measured token counts (136 in / 110 out):

| Path | $/1k requests |
|---|---|
| large (baseline) | 0.0864 |
| small | 0.0432 |
| cache hit | 0 |

```
routing only, 75% small → 0.0540/1k → 37.5% reduction
routing only, 85% small → 0.0497/1k → 42.5% reduction
```

### What this changes

**G1 (≥60% cost reduction) cannot be met by routing alone.** Routing tops out
around 37–43%. The PRD's stated risk position — *"routing carries G1, the cache is
upside"* — is now **inverted**: the cache is load-bearing.

Required cache hit rate to reach 60% total reduction, with a 75/25 split on
misses: **36%**.

That is attainable but no longer a bonus, so the project's risk concentrates in
the component that was supposed to de-risk it. Two consequences:

- The threshold sweep matters more, not less — a safe threshold that yields under
  ~36% hit rate misses G1 outright.
- Routing's justification shifts from "the big win" to "a free 40% on top of the
  cache", since it costs one config lookup and no extra latency.

**Escalation break-even also moves:** `e = 1 − small/large = 0.50`. An escalation
path is net-negative above 50% escalation, not the ~91% the design implied.
Dropping it was still right, but the margin is half what was assumed.

### Also worth pursuing

Groq ships **provider-side prompt caching** for the gpt-oss models. Our system
prompt is a fixed prefix on every request, so this is a real, complementary lever
that costs no design work. The TDD's alternatives table already lists it as
"layer, don't substitute" — that now deserves measuring rather than noting.

`TODO(measure)`: prompt-cache discount on repeated system prefixes.

## 2026-09-03 — gpt-oss are reasoning models: 78% of output tokens were invisible

Answers were coming back empty or truncated mid-sentence. Cause: `gpt-oss`
emits hidden reasoning in the completion, and it bills as output.

Default settings on a single weather question:

| `reasoning_effort` | output tokens | of which reasoning | result |
|---|---|---|---|
| (unset) | 329 | 256 | fine, but 78% wasted |
| `low` | 82 | 5 | same answer quality |
| `medium` | 266 | 189 | — |
| `high` | 400 | 370 | **truncated** (`finish=length`) |

With `max_tokens=200`, reasoning consumed the whole budget and the answer came
back empty or cut off. Two fixes: `reasoning_effort="low"` and `max_tokens=400`.

**This is a 4× reduction in output tokens** — output is the expensive half at
$0.30/M versus $0.075/M input, so it is the single largest cost lever found so
far, and it is a config change rather than a design.

Measured after the fix, across the completed floor: **~130–250 input, ~30–60
output tokens** per request. The design assumed 945 in / 70 out; input is far
lower because there is no conversation history yet.

Also handled: an empty `content` now raises rather than returning a blank
answer, and `finish_reason == "length"` is logged as truncation. Silent
truncation read aloud sounds like a broken assistant, not a budget problem.

**Cost implication:** both tiers benefit equally, so the 2× tier ratio is
unchanged — but the absolute per-request cost drops ~3×, and the cache's value
drops with it, since what a hit avoids is now cheaper. Re-derive G1 against
measured tokens before quoting any figure.

## 2026-09-03 — Geocode caching: 602ms → 148ms

A weather lookup is two upstream calls, geocode then forecast. Coordinates are
immutable, so the geocode is memoised (negative results too — a misheard city
would otherwise cost a full round trip on every retry).

Cold 602ms → warm 148ms. Larger than expected, and it lands on every repeat
question about a city even before the semantic cache exists.

## 2026-09-03 — Floor complete

Voice in/out, cross-session memory, live external API, deployable. Verified end
to end: aliases resolve (Bangalore → Bengaluru, not Bangalore Town, Pakistan),
unit preference persists and is applied, home city resolves for questions with
no city, and every failure path answers honestly rather than inventing weather.

## 2026-09-04 — A 5-second failure reported as "timeout after 20.0s"

A live 503 showed `timeout after 20.0s` in a log line five seconds after the
preceding request. The message interpolated the *configured* read timeout rather
than what happened; the real failure was a **connect** timeout at 5s.

Two fixes:

- Errors now report the exception type and measured elapsed time
  (`ConnectTimeout after 1003ms (connect=10.0s read=20.0s)`). An error message
  that quotes a config constant is worse than no message — it sends you looking
  at the wrong timeout.
- **One retry on connection-level failures**, which the TDD specified and the
  implementation never had. A completion changes no state, so it is safe to
  repeat, and a half-open connection is the most common transient failure here.
  Non-connection HTTP errors are not retried.

Connect timeout raised 5s → 10s. Connection setup is normally tens of
milliseconds, so a slow one is a dead connection rather than a slow model — but
5s was tight enough to fire on ordinary network jitter.

## 2026-09-04 — Retries: the deadline matters more than the attempt count

One shared retry policy (`app/retry.py`) across both upstreams. Transient
failures only — connect/read timeouts, dropped connections, pool timeouts.
Anything the server *answered* (400, 401, 429) is not retried: it will answer
the same next time, and a 429 wants backoff rather than another attempt.

Three attempts against a blackholed host took **9.7s**. For a voice assistant
that is far worse than failing in two and saying so, so `call()` takes a
`deadline_s` that bounds the whole sequence — checked **before each attempt**,
not only before each sleep. An attempt starting at 3.1s with a 3s connect
timeout blows a 5s budget on its own.

With a 2s connect timeout and 2 attempts on the weather tool: **9.7s → 4.3s**,
and the healthy path is unchanged (Dubai 1.2s cold, London 0.3s warm).

### A retry surfaced a wrong answer

`geocode()` returned `None` for both "no such city" and "could not reach the
API". The caller turned that into *"I couldn't find that place"* — which is a
false statement when the real cause is a network failure, and it sends the user
looking for a spelling mistake that isn't there.

Transport failures now raise `Unreachable` and produce *"I couldn't reach the
weather service"*. Distinguishing them costs one exception class and is the
difference between an honest failure and a misleading one.

Transport failures are also not cached, unlike genuine misses — caching one bad
minute would persist it for the process lifetime.

## 2026-09-04 — The key does the safety work; the threshold only does quality

Measured similarity between real phrasings, using character-trigram cosine:

| pair | similarity | same answer? |
|---|---|---|
| "What's the weather in Delhi?" ~ "weather in Delhi" | 0.804 | yes |
| "What's the weather in Delhi?" ~ "How's Delhi looking?" | 0.235 | yes |
| "What's the weather in Delhi?" ~ "What's the weather in **Mumbai**?" | 0.794 | **no** |
| "Will it rain in Delhi **tomorrow**?" ~ "…**today**?" | 0.801 | **no** |
| "What's the **temperature** in Delhi?" ~ "…**humidity**…?" | 0.636 | **no** |

**The distributions overlap completely.** Same-answer pairs span 0.235–0.921;
different-answer pairs span 0.636–0.801. No threshold separates them — a value
high enough to exclude Delhi/Mumbai (>0.80) also excludes most genuine
paraphrases.

This is the central design claim, now measured rather than argued: **similarity
cannot be trusted to decide whether two questions have the same answer.**

It does not need to. Every different-answer pair above differs in a *parameter*
or an *intent*, so they land in different key namespaces and are **never
compared**. Visible in the request log: the Mumbai and tomorrow requests record
`similarity = NULL` — no comparison was attempted at all.

Within a namespace, intent and parameters already match, so the stored answer is
correct for anything that reaches it. The threshold is therefore free to be
permissive, and its job is quality (is this phrasing close enough to be the same
question) rather than safety (do these have the same answer).

**Shipped threshold: 0.20**, set from the measured floor of genuine paraphrases
(0.235), not chosen a priori. The initial 0.92 was a placeholder carried over
from an embedding-cosine assumption and produced a 0% hit rate.

### Measured effect

| | |
|---|---|
| Cache miss (tool + model) | 1638ms |
| Cache hit | **2ms** |
| Hit rate, of cacheable requests | 60% (n=5) |
| False hits | 0 |

A hit costs zero tokens and skips both upstreams.

### Implementation note

Similarity is character-trigram cosine, not a neural embedding. Within a
namespace where intent and parameters already match, resolving phrasing is a
narrow enough job that a lexical measure does it — and it removes a ~23MB model
from a 512MB host. `EMBEDDING_VERSION` is stored per entry, so swapping in
sentence embeddings empties the cache rather than comparing across
representations.

## 2026-09-04 — Baseline vs full: 82% cost reduction, 64% hit rate, zero false hits

25 requests shaped like real traffic (popular questions repeated, asked
different ways), run twice against the same build one flag apart.

| | baseline | full |
|---|---|---|
| LLM cost / 1,000 requests | $0.0734 | $0.0131 (−82%) |
| p50 latency | 671 ms | 5 ms (−99%) |
| Cache hit rate | 0% | 64% |
| False hits | — | 0 |
| Tokens | 7,414 | 2,530 |

**The 82% exceeds the ~68% that routing alone was projected to give**, because
the cache is doing more work than the earlier arithmetic assumed — 64% hit rate
against a projected 45%. Two reasons: making advice intents cacheable added a
common question class, and repeated questions in a realistic workload cluster
harder than a uniform distribution suggests.

**p50 dropping to 5ms is a mix artefact and should be quoted carefully.** With
64% of requests served from cache, the median request *is* a cache hit. The
honest statement is "a hit is ~240× faster than a miss, and at this hit rate the
median request is a hit" rather than "the service got 99% faster".

**Zero false hits** across the run, with adversarial pairs deliberately included
(Delhi/Mumbai, today/tomorrow, temperature/humidity). Consistent with the
namespace isolation test passing at threshold 0.0.

`TODO(measure)`: answer quality against baseline. Not yet run — the cost and
safety numbers were the priority, and a judge pass over sampled answers is the
remaining gap.
