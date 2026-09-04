# Day 3 — working note

Not a deliverable. Scratch plan for tomorrow, uncommitted on purpose.
Written 2026-09-04 after finishing the cache.

## Where things stand

Working and verified locally:

- Floor: voice in/out, cross-session memory, live Open-Meteo, honest failures
- Model routing, small vs large tier
- Semantic cache: 1638ms miss → 2ms hit, 0 false hits observed
- Deferred questions: "where do you live?" → the reply answers the original
- `run.sh`: background service, watches git HEAD, restarts on pull
- 37 classifier checks passing

Not done:

- **Nothing is deployed.** No public URL yet
- **Branch is not pushed** (history was rewritten, needs `--force-with-lease`)
- Threshold sweep not run — the curve is the deep dive's headline artefact
- No baseline-vs-full comparison numbers
- README results table is still `TODO`
- No deck, no Loom

## Order for tomorrow

Deployment first, while it is the only thing changing. Measurement second,
because it produces what the deck is about. Deck last, and rehearsed.

### 1. Push and deploy · ~45min · needs you

```bash
git push --force-with-lease origin initial_implementation
```

Render → New Web Service → connect repo → it reads the Dockerfile.
Only required env var: `GROQ_API_KEY`. Everything else defaults correctly now.

Then:

- `curl https://<app>.onrender.com/health` — all five components ok
- Open in Chrome, run: Delhi → "how's Delhi looking?" → Mumbai
- Set a cron ping to `/health` every 10 min (cold start is 30–60s)

Known and accepted: SQLite is on an ephemeral filesystem, so memory resets on
every deploy. Fine for a demo; say so in the README rather than engineering
around it.

### 2. Measurement · ~1h · local, not deployed

Local, deliberately: no rate-limit pacing, no cold starts in the numbers, and
`request_log` survives.

```bash
curl -X POST localhost:8000/admin/flush     # start cold
```

Drive ~60 requests through the app. Composition that matters:

- ~25 intents, popular ones repeated (that is where hits come from)
- ~10 adversarial pairs: Delhi/Mumbai, today/tomorrow, temperature/humidity
- **phrase them the way a person speaks.** Every bug found so far came from a
  natural sentence, not a test-shaped one. Tidy phrasings will report an
  optimistic hit rate

Then the same phrases with `X-Sarjy-Mode: baseline` for the comparison.

Pull the numbers:

```bash
curl -s localhost:8000/metrics | python3 -m json.tool
sqlite3 sarjy.db "SELECT mode, count(*), sum(cached), avg(latency_ms),
                         sum(tokens_in), sum(tokens_out)
                  FROM request_log GROUP BY mode"
```

**Threshold sweep** — replays logged `similarity` values, costs zero upstream
calls. For t in 0.10…0.95: how many hits, and how many of those returned an
entry whose intent+params differ from the request (a false hit). Two curves.

Expect the honest finding: false-hit rate stays at zero across the whole range,
because the key namespace already separated everything. That *is* the result —
the threshold is a quality knob, not a safety one.

### 3. Write-up · ~45min

- README results table: replace the `TODO`s with measured numbers
- `docs/FINDINGS.md` already has most of it — add the sweep and the
  baseline-vs-full comparison
- State the sample size. 60 requests supports "it works, here is the error
  rate", not a hit rate to two decimals

### 4. Deck + Loom · ~1.25h

Five slides. Suggested shape for five minutes:

1. What it is — 30s, live demo: Delhi, paraphrase hits, Mumbai misses
2. The problem — the model is a slow metered dependency, traffic is repetitive
3. **The finding** — similarity cannot decide whether two questions share an
   answer (0.235 for a paraphrase vs 0.794 for Delhi/Mumbai). The key does the
   safety work
4. Numbers — cost, latency, hit rate, false-hit rate
5. What I would do next — and what the bonus design covers

Record the Loom and send it, with the repo link, **before** the meeting. Then
rehearse against a timer. They stop you 30 seconds past five.

### 5. Daily update to Sarj

The brief asks for one per day. Send tonight or first thing: what landed, the
live URL once it exists, and that the deep dive is caching and routing with
measured numbers coming.

## Cut order if short

1. UI polish — the cache-hit indicator already exists, leave it
2. Sweep from 20 threshold values to 5
3. Measurement from 60 requests to 30, state the sample size
4. Last resort: present routing alone, cache described but unmeasured

**Not cuttable:** the deployed URL, one measured comparison, the deck.

## Things deliberately not being done

- 7-day forecast. Currently answers today and tomorrow, and says so plainly for
  anything further out. Extending is ~20min if a demo question needs it
- Postgres. SQLite is enough; the ephemeral caveat is stated
- Provider prompt caching. Real and complementary, but it is a second result
  competing with the first for five minutes of airtime
