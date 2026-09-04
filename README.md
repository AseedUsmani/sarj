# Sarjy

A voice assistant for weather, and a semantic cache + model router in front of the
LLM that powers it.

**Live demo:** <https://sarj-aseed.onrender.com/> — Chrome for voice, typing works anywhere
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

Measured 2026-09-04 over 25 requests shaped like real traffic — popular
questions repeated, asked different ways. `baseline` and `full` are the same
code one flag apart, so the comparison is not against a remembered number.

| | baseline | cache + routing |
|---|---|---|
| LLM cost per 1,000 requests | $0.0734 | **$0.0131** — 82% lower |
| p50 latency | 671 ms | **5 ms** — 99% lower |
| Cache hit rate | 0% | **64%** |
| **False hits** | — | **0** |
| Tokens consumed | 7,414 | 2,530 |

A cache hit costs ~3 ms and zero tokens, and skips *both* upstreams — the
weather API and the model. Latency saving comes from both; cost saving is
entirely the model, since Open-Meteo is free.

**The finding underneath the numbers.** Similarity cannot decide whether two
questions have the same answer. Measured on real phrasings:

| pair | similarity | same answer? |
|---|---|---|
| "weather in Delhi" ~ "How's Delhi looking?" | 0.235 | yes |
| "weather in Delhi" ~ "weather in **Mumbai**" | 0.794 | **no** |
| "rain in Delhi **tomorrow**" ~ "…**today**" | 0.801 | **no** |

The distributions overlap: genuine paraphrases score *lower* than
different-answer pairs, so no threshold separates them. It does not need to —
parameters go in the cache key, so those pairs land in different namespaces and
are never compared. **The key does the safety work; the threshold only decides
whether a phrasing is the same question.**

That is asserted by a test rather than by argument: `tests/test_cache.py` sets
the similarity threshold to `0.0`, where every entry looks like a perfect match,
and asserts a request still never receives an entry from another namespace. If
separation holds at 0.0 it is structural, and no threshold change can introduce
a cross-parameter false hit.

Full measurements, including the assumptions they overturned, are in
[docs/FINDINGS.md](docs/FINDINGS.md).

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
| Service: voice, memory, weather tool | **done** — the assignment's floor |
| Model routing (small/large tiers) | done |
| Semantic cache | not started |
| Deployment | not started |
| Measurement run | not started |

Measured findings so far are in [docs/FINDINGS.md](docs/FINDINGS.md), including
two that changed the design: the model tiers are latency-indistinguishable and
priced 2x apart rather than the ~12x assumed.

## Running it

```bash
cp .env.example .env               # add GROQ_API_KEY — nothing else is required
pip install -r requirements.txt
./run.sh                           # background, logs to logs/sarjy.log
```

Open <http://localhost:8000>. Use `localhost`, not the machine IP: the Web Speech
API only grants microphone access on `localhost` or HTTPS.

`run.sh` starts the service and a watcher that restarts it when git `HEAD` moves,
so a `git pull` is enough — and it reinstalls dependencies first if
`requirements.txt` changed.

| | |
|---|---|
| `./run.sh` | start (or restart) in the background |
| `./run.sh status` | pids, current commit, live `/health` |
| `./run.sh logs` | follow the log |
| `./run.sh stop` | stop the service and the watcher |
| `./run.sh foreground` | attached, with `--reload`, no watcher |

No database to provision — SQLite, created at boot. Only `GROQ_API_KEY` is
required; every other setting has a working default, including the similarity
threshold, which defaults to the measured value rather than a placeholder.

### Trying the cache

```bash
curl -X POST localhost:8000/admin/flush          # start cold
```

Then ask *"What's the weather in Delhi?"*, then *"How's Delhi looking?"* — the
second is served from cache. Ask about Mumbai and it misses again, because a
different parameter is a different namespace.

`/metrics` reports hit rate, routing split, tokens and latency for the run.

### Tests

```bash
python3 tests/test_classifier.py    # 40 fixtures: intents and entity extraction
python3 tests/test_cache.py         # 7 checks: keys, isolation, expiry, threshold
```

`test_cache.py` includes the assertion the design rests on — namespace isolation
holds with the similarity threshold set to `0.0`, where every entry looks like a
perfect match. Neither test needs network or a model.

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
