# Deployment

One container, one process, no database to provision. The service is stateless
apart from a SQLite file, so deploying is: push, point Render at the repo, set
one environment variable.

## Prerequisites

- A Groq API key — <https://console.groq.com/keys>
- The branch pushed to GitHub
- Nothing else. No database signup, no object storage, no queue

Open-Meteo needs no key, which is part of why it was chosen: a reviewer can run
this repo without asking anyone for a credential.

## Render

Render is used because it builds a Dockerfile directly and supports the free
tier this project runs on. Nothing in the design depends on it — the container
runs anywhere that can run a container.

### 1. Create the service

**New +** → **Web Service** → connect the repository.

| Field | Value |
|---|---|
| Branch | `initial_implementation` |
| Runtime | Docker (detected — leave it) |
| Build command | *empty* |
| Start command | *empty* |
| Instance type | Free |
| Health check path | `/health` |

Leave build and start commands empty. Filling them overrides the Dockerfile,
which already installs dependencies and starts uvicorn against `${PORT}`.

### 2. Environment

Only one variable is required:

```
GROQ_API_KEY = gsk_...
```

Every other setting has a working default in `app/config.py`. In particular
`SIMILARITY_THRESHOLD` defaults to the measured value (0.20, see
[FINDINGS](FINDINGS.md)), not the placeholder it was developed against.

**Do not set `PORT`.** Render injects it and the container reads `${PORT}`.

Optional overrides:

| Variable | Default | When to change it |
|---|---|---|
| `MODE` | `full` | `baseline` or `router` to serve a comparison build |
| `SIMILARITY_THRESHOLD` | `0.20` | After re-running the sweep |
| `CONFIDENCE_FLOOR` | `0.6` | Below this a turn is uncacheable and uses the large tier |
| `SMALL_MODEL` / `LARGE_MODEL` | gpt-oss 20b / 120b | Provider renames a model |
| `SQLITE_PATH` | `./sarjy.db` | Pointing at a mounted disk (see below) |
| `RATE_LIMIT_PER_MIN` | `60` | Per session, on a public endpoint |

Mode is also overridable per request with the `X-Sarjy-Mode` header, so A/B runs
need no redeploy.

### 3. Verify

```bash
curl -s https://<app>.onrender.com/health | python3 -m json.tool
```

All five components should report ok:

```json
{"ok": true, "mode": "full",
 "components": {"db": "ok (sqlite:sarjy.db)", "groq": "configured",
                "classifier": "ok (rules-v1)", "weather": "ok (live)",
                "cache": "ok (0 entries, 0 namespaces, t=0.2)"}}
```

`groq: unconfigured` means the environment variable did not save. Components
that are not wired report their real state rather than `ok` — the probe is not
allowed to overstate readiness.

Then open the URL in Chrome and run the demo path:

```
"What's the weather in Delhi?"    → served from model
"How's Delhi looking?"            → served from cache
"What's the weather in Mumbai?"   → served from model (different namespace)
```

## Operational notes

### Cold starts

The free tier sleeps after ~15 minutes idle and takes 30–60s to wake. Ping
`/health` every 10 minutes from a free scheduler (cron-job.org) to prevent it.

**Open the URL an hour before any demo.** A cold start costs a fifth of a
five-minute slot.

### State is ephemeral

The SQLite file lives on the container filesystem, which is destroyed on every
deploy. Consequences:

- Cross-session memory works within a deployment and resets when you push
- `request_log` is lost on deploy, so **measurement runs belong locally**, not
  against the deployed instance
- The cache is derived and rebuilds itself from traffic

This is accepted rather than worked around. To make state durable, mount a
Render persistent disk and set `SQLITE_PATH=/data/sarjy.db`.

### Rollback

Redeploy the previous image from the Render dashboard. The cache is a derived
store and safe to lose; the schema is applied at boot with
`CREATE TABLE IF NOT EXISTS`, so it is safe to run on any deploy, forwards or
backwards.

### Logs

stdout, structured JSON, one line per request. Visible in the Render dashboard.

### Scaling

**Single instance, deliberately.** The cache index is in process, so two
replicas would hold two independent caches: hit rate roughly halves and the same
question resolves differently depending on which instance answers. For a service
whose output is a measured hit rate, that makes the measurement meaningless.

Scaling out requires moving the index to a shared store — pgvector or Redis —
which is a change to `app/cache/store.py` and nothing else. See
[TDD §13](TDD.md).

## Running it locally

```bash
cp .env.example .env          # add GROQ_API_KEY
pip install -r requirements.txt
./run.sh                      # background, watches git HEAD, logs to logs/
```

`./run.sh status` for state, `./run.sh logs` to follow, `./run.sh stop` to stop.
The watcher restarts the service when HEAD moves, so a `git pull` is enough —
and it reinstalls dependencies first if `requirements.txt` changed.

`./run.sh foreground` runs it attached with `--reload` instead.

Use `http://localhost:8000`, not the machine IP: the Web Speech API only grants
microphone access on `localhost` or HTTPS.

## Cost

Free tier throughout: Render free instance, no database service, Open-Meteo
free, Groq free tier (1,000 requests/day, 8,000 tokens/minute — measured from
response headers 2026-09-04).

Render's smallest paid instance (~$7/month) removes cold starts and doubles the
memory headroom. For a demo that matters, that is the cheapest insurance in the
project.

Note this is infrastructure cost, and separate from the per-request inference
cost the deep dive measures.
