"""FastAPI application.

Step 1: health and the static client only. Deployed before any feature work so
that deployment problems and application bugs never arrive together
(docs/TDD.md §13).
"""
import logging
import os
from pathlib import Path

import re

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import cache, classifier, db, llm, pipeline
from app.tools import weather
from app.config import VALID_MODES, settings

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":%(message)r}',
)
log = logging.getLogger("sarjy")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Sarjy", version=settings.git_sha)


@app.on_event("startup")
async def startup() -> None:
    log.info(
        "starting mode=%s tool_source=%s sha=%s",
        settings.mode, settings.tool_source, settings.git_sha,
    )
    await db.init()
    await cache.init()


@app.on_event("shutdown")
async def shutdown() -> None:
    await llm.aclose()
    await weather.aclose()
    await db.close()


SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    text: str = Field(min_length=1)


def _error(status: int, code: str, message: str, trace_id: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "trace_id": trace_id}},
    )


@app.get("/metrics")
async def metrics() -> JSONResponse:
    """Counters for the current run, read from request_log."""
    from sqlalchemy import text as _text
    async with db.engine().connect() as conn:
        rows = (await conn.execute(_text(
            """SELECT mode, count(*) n,
                      sum(cached) hits,
                      sum(cacheable) cacheable,
                      sum(coalesce(tokens_in,0)) tin,
                      sum(coalesce(tokens_out,0)) tout,
                      avg(latency_ms) avg_ms
               FROM request_log GROUP BY mode"""))).fetchall()
    by_mode = {
        r[0]: {
            "requests": r[1], "cache_hits": r[2] or 0, "cacheable": r[3] or 0,
            "hit_rate_of_cacheable": round((r[2] or 0) / r[3], 3) if r[3] else None,
            "tokens_in": r[4] or 0, "tokens_out": r[5] or 0,
            "avg_latency_ms": round(r[6] or 0, 1),
        } for r in rows
    }
    return JSONResponse({"by_mode": by_mode, "cache": cache.stats()})


@app.post("/admin/flush")
async def admin_flush() -> JSONResponse:
    """Empties the cache. The demo needs this to be repeatable: rehearsing warms
    the cache, and a warm cache turns the live 'miss' into a hit."""
    return JSONResponse(await cache.flush())


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic's default 422 body does not match the API contract, so it is
    translated into the uniform error shape (docs/TDD.md §3)."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", ())[1:]) or "body"
    return _error(400, "invalid_request", f"{field}: {first.get('msg', 'invalid')}")


@app.post("/chat")
async def chat(
    body: ChatRequest,
    x_sarjy_mode: str | None = Header(default=None, alias="X-Sarjy-Mode"),
) -> JSONResponse:
    if not SESSION_RE.match(body.session_id):
        return _error(400, "invalid_request", "session_id must be 8-64 chars [A-Za-z0-9_-]")

    text = body.text.strip()
    if not text:
        return _error(400, "invalid_request", "text must not be empty")
    if len(text) > settings.max_text_chars:
        return _error(
            400, "invalid_request", f"text exceeds {settings.max_text_chars} characters"
        )

    # Per-request mode override, so A/B runs need no redeploy (docs/TDD.md).
    mode = x_sarjy_mode or settings.mode
    if mode not in VALID_MODES:
        return _error(400, "invalid_request", f"mode must be one of {list(VALID_MODES)}")

    try:
        answer = await pipeline.handle(body.session_id, text, mode)
    except pipeline.PipelineError as exc:
        return _error(exc.status, exc.code, str(exc), exc.trace_id)
    except Exception as exc:  # noqa: BLE001 - last resort, never leak internals
        log.exception("unhandled error")
        return _error(500, "internal", "unexpected error")

    return JSONResponse(answer.to_dict())


@app.get("/health")
async def health() -> JSONResponse:
    """Readiness probe and keep-warm target (TDD §13).

    Reports what is actually wired up. Components not yet built report
    'not_implemented' rather than 'ok', so the probe never overstates readiness.
    """
    return JSONResponse(
        {
            "ok": True,
            "mode": settings.mode,
            "sha": settings.git_sha,
            "components": {
                "db": await db.status(),
                "groq": await llm.status(),
                "classifier": classifier.status(),
                "weather": await weather.status(),
                "cache": cache.status(),
            },
        }
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
