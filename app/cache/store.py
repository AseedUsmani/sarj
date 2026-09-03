"""Durability for the cache.

The table is not the cache — the index (`app.cache.index`) is. This module only
loads the index at boot and writes through on change, so nothing here is on the
request path.

Entries whose `embedding_version` differs from the live vectoriser are skipped
at load: vectors are only comparable within one representation, so a change
empties the cache rather than corrupting it.
"""
import json
import logging
import time
from typing import Iterable, Optional

from sqlalchemy import text

from app import db
from app.cache.index import EMBEDDING_VERSION, Entry

log = logging.getLogger("sarjy.cache.store")

_SELECT = text(
    """SELECT id, cache_key, question, answer, embedding, expires_at, hits
       FROM cache_entries
       WHERE embedding_version = :ev AND expires_at > :now"""
)
_INSERT = text(
    """INSERT INTO cache_entries
       (cache_key, question, answer, embedding, embedding_version, hits, expires_at)
       VALUES (:k, :q, :a, :e, :ev, 0, :exp)"""
)
_DELETE_ALL = text("DELETE FROM cache_entries")
_DELETE_EXPIRED = text("DELETE FROM cache_entries WHERE expires_at <= :now")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


def _epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return time.mktime(time.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0


async def load_all() -> list[Entry]:
    """Read live entries at boot. Failure here is not fatal: an empty cache is
    a slow cache, not a broken service."""
    try:
        async with db.engine().connect() as conn:
            rows = (await conn.execute(
                _SELECT, {"ev": EMBEDDING_VERSION, "now": _iso(time.time())}
            )).fetchall()
    except Exception as exc:
        log.warning("cache load failed, starting empty: %s", exc)
        return []

    entries = []
    for row in rows:
        try:
            vector = json.loads(row[4]) if row[4] else {}
        except (ValueError, TypeError):
            vector = {}
        entries.append(Entry(
            key=row[1], question=row[2], answer=row[3],
            expires_at=_epoch(row[5]), vector=vector,
            hits=row[6] or 0, row_id=row[0],
        ))
    log.info("cache loaded entries=%d version=%s", len(entries), EMBEDDING_VERSION)
    return entries


async def persist(entry: Entry) -> None:
    """Write-through. Best effort: losing durability must never fail a request
    that has already been answered."""
    try:
        async with db.engine().begin() as conn:
            await conn.execute(_INSERT, {
                "k": entry.key, "q": entry.question, "a": entry.answer,
                "e": json.dumps(entry.vector), "ev": EMBEDDING_VERSION,
                "exp": _iso(entry.expires_at),
            })
    except Exception as exc:
        log.warning("cache persist failed key=%s: %s", entry.key, exc)


async def delete_all() -> int:
    async with db.engine().begin() as conn:
        result = await conn.execute(_DELETE_ALL)
    return result.rowcount or 0


async def delete_expired() -> int:
    async with db.engine().begin() as conn:
        result = await conn.execute(_DELETE_EXPIRED, {"now": _iso(time.time())})
    return result.rowcount or 0
