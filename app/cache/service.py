"""Module-level cache singleton and the operations the pipeline calls."""
import logging
import time
from typing import Optional

from app.cache import store
from app.cache.index import CacheIndex, Entry
from app.cache.keys import build_key
from app.config import settings

log = logging.getLogger("sarjy.cache")

_index: Optional[CacheIndex] = None


async def init() -> None:
    global _index
    _index = CacheIndex(threshold=settings.similarity_threshold)
    for entry in await store.load_all():
        _index.add(entry)


def index() -> CacheIndex:
    if _index is None:
        raise RuntimeError("cache.init() has not run")
    return _index


def enabled() -> bool:
    return _index is not None


def lookup(key: str, question: str) -> tuple[Optional[Entry], Optional[float]]:
    return index().lookup(key, question)


async def store_answer(key: str, question: str, answer: str, ttl_seconds: int) -> None:
    if not answer or ttl_seconds <= 0:
        return
    entry = index().add(Entry(
        key=key, question=question, answer=answer,
        expires_at=time.time() + ttl_seconds,
    ))
    await store.persist(entry)


async def flush() -> dict:
    removed_memory = index().clear()
    removed_rows = await store.delete_all()
    log.info("cache flushed memory=%d rows=%d", removed_memory, removed_rows)
    return {"evicted": removed_memory, "rows_deleted": removed_rows}


def status() -> str:
    if _index is None:
        return "uninitialised"
    s = _index.stats()
    return f"ok ({s['entries']} entries, {s['namespaces']} namespaces, t={s['threshold']})"


def stats() -> dict:
    return index().stats()
