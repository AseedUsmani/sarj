"""Connection and schema — SQLite.

Deliberately single-dialect. A Postgres/SQLite abstraction bought nothing at this
scale except two spellings of every DDL statement, and the cache index lives in
process memory (docs/TDD.md §10.2), so the database is a durability log rather
than something on the request path.

Schema is applied at boot with CREATE TABLE IF NOT EXISTS. No migration tool;
that is a stated gap, not an oversight.

Deployment note: on a host with an ephemeral filesystem the file is lost on every
deploy, which resets cross-session memory. Mount a persistent disk, or accept the
reset and say so.
"""
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings

log = logging.getLogger("sarjy.db")

_engine: Optional[AsyncEngine] = None

DDL = [
    """CREATE TABLE IF NOT EXISTS facts (
        session_id TEXT NOT NULL,
        key        TEXT NOT NULL,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (session_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        email         TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS cache_entries (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        cache_key         TEXT NOT NULL,
        question          TEXT NOT NULL,
        answer            TEXT NOT NULL,
        embedding         BLOB,
        embedding_version TEXT NOT NULL DEFAULT '',
        hits              INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at        TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS request_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id    TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        mode        TEXT NOT NULL,
        intent      TEXT,
        confidence  REAL,
        cacheable   BOOLEAN NOT NULL DEFAULT 0,
        cached      BOOLEAN NOT NULL DEFAULT 0,
        cache_key   TEXT,
        similarity  REAL,
        route       TEXT,
        tokens_in   INTEGER,
        tokens_out  INTEGER,
        tool_called BOOLEAN NOT NULL DEFAULT 0,
        latency_ms  INTEGER NOT NULL,
        error_code  TEXT,
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)",
    "CREATE INDEX IF NOT EXISTS ix_cache_lookup ON cache_entries (cache_key, expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_cache_version ON cache_entries (embedding_version)",
    "CREATE INDEX IF NOT EXISTS ix_log_run ON request_log (mode, created_at)",
]

# Concurrent readers alongside a writer, and a durability/latency trade that is
# right for a log: WAL plus NORMAL sync means a crash can lose the last commits,
# which for request_log and a rebuildable cache is acceptable.
PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
]


async def init() -> None:
    global _engine
    path = Path(settings.sqlite_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(f"sqlite+aiosqlite:///{path}", pool_pre_ping=True)
    async with _engine.begin() as conn:
        for pragma in PRAGMAS:
            await conn.execute(text(pragma))
        for stmt in DDL:
            await conn.execute(text(stmt))
    log.info("schema ready path=%s", path)


async def close() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("db.init() has not run")
    return _engine


async def status() -> str:
    """For /health. Never raises — a probe that throws is a probe that lies."""
    if _engine is None:
        return "uninitialised"
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return f"ok (sqlite:{Path(settings.sqlite_path).name})"
    except Exception as exc:
        log.warning("db health check failed: %s", exc)
        return "error"
