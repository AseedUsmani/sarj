"""FastAPI application.

Step 1: health and the static client only. Deployed before any feature work so
that deployment problems and application bugs never arrive together
(docs/TDD.md §13).
"""
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings

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
                "db": "configured" if settings.database_url else "unconfigured",
                "groq": "configured" if settings.groq_api_key else "unconfigured",
                "cache": "not_implemented",
                "classifier": "not_implemented",
            },
        }
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
