"""Per-request log rows.

Every number the deep dive publishes is computed from this table, so a row is
written for every request including failures. Writing is best-effort: a logging
failure must never turn a served answer into an error.
"""
import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

from sqlalchemy import text

from app import db

log = logging.getLogger("sarjy.reqlog")

INSERT = text(
    """INSERT INTO request_log
       (trace_id, session_id, mode, intent, confidence, cacheable, cached,
        cache_key, similarity, route, tokens_in, tokens_out, tool_called,
        latency_ms, error_code)
       VALUES
       (:trace_id, :session_id, :mode, :intent, :confidence, :cacheable, :cached,
        :cache_key, :similarity, :route, :tokens_in, :tokens_out, :tool_called,
        :latency_ms, :error_code)"""
)


@dataclass
class LogRow:
    trace_id: str
    session_id: str
    mode: str
    latency_ms: int
    intent: Optional[str] = None
    confidence: Optional[float] = None
    cacheable: bool = False
    cached: bool = False
    cache_key: Optional[str] = None
    similarity: Optional[float] = None
    route: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tool_called: bool = False
    error_code: Optional[str] = None


async def write(row: LogRow) -> None:
    try:
        async with db.engine().begin() as conn:
            await conn.execute(INSERT, asdict(row))
    except Exception as exc:
        # Losing a metrics row is bad; failing a served request over it is worse.
        log.warning("request_log write failed trace=%s: %s", row.trace_id, exc)
