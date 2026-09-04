"""Cross-session memory.

A write path and a read path. No inference here: the classifier already returns
`set_home_city` with the city extracted, so recognising the utterance is its
job and persisting the value is ours.

Facts are a current preference, not a history — last write wins. Superseding
versions with validity intervals is a real design and out of scope; see
bonus_assignment/.
"""
import logging
from typing import Optional

from sqlalchemy import text

from app import db

log = logging.getLogger("sarjy.memory")

#: Only these keys are storable. An open key space would let a classifier bug
#: write arbitrary rows.
ALLOWED = ("home_city", "unit")

_SELECT = text("SELECT key, value FROM facts WHERE session_id = :sid")
_DELETE = text("DELETE FROM facts WHERE session_id = :sid AND key = :k")
_INSERT = text(
    "INSERT INTO facts (session_id, key, value) VALUES (:sid, :k, :v)"
)


async def load(owner: str) -> dict[str, str]:
    """Read once per request; retrieval is then a dict lookup, not a query.

    `owner` is a raw session id for an anonymous browser, or `user:<id>` once
    signed in. The column is still called session_id -- renaming it would need a
    migration and buy nothing.
    """
    try:
        async with db.engine().connect() as conn:
            rows = (await conn.execute(_SELECT, {"sid": owner})).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception as exc:
        # Memory is an enhancement, never a reason to fail a request.
        log.warning("fact load failed for %s: %s", owner, exc)
        return {}


async def put(owner: str, key: str, value: str) -> bool:
    if key not in ALLOWED or not value:
        return False
    value = value.strip()[:120]
    try:
        # Delete-then-insert rather than dialect-specific upsert syntax, so the
        # same statement works on Postgres and SQLite.
        async with db.engine().begin() as conn:
            await conn.execute(_DELETE, {"sid": owner, "k": key})
            await conn.execute(_INSERT, {"sid": owner, "k": key, "v": value})
        log.info("learned %s=%s for %s", key, value, owner)
        return True
    except Exception as exc:
        log.warning("fact write failed: %s", exc)
        return False


def home_city(facts: dict[str, str]) -> Optional[str]:
    return facts.get("home_city")


def unit(facts: dict[str, str]) -> str:
    return facts.get("unit", "celsius")


def describe(facts: dict[str, str]) -> str:
    """Rendered for a recall_fact answer. Plain, because it is read aloud."""
    if not facts:
        return "I don't know anything about you yet."
    bits = []
    if facts.get("home_city"):
        bits.append(f"your home city is {facts['home_city'].title()}")
    if facts.get("unit"):
        bits.append(f"you prefer {facts['unit']}")
    return "You told me " + " and ".join(bits) + "."


async def adopt(from_owner: str, to_owner: str) -> int:
    """Carry an anonymous session's facts onto an account at sign-in.

    Existing account facts win: signing in on a new browser must not overwrite
    what the account already knows with whatever that browser happened to pick
    up. Returns the number of facts adopted.
    """
    if from_owner == to_owner:
        return 0
    source = await load(from_owner)
    if not source:
        return 0
    existing = await load(to_owner)
    adopted = 0
    for key, value in source.items():
        if key in existing:
            continue
        if await put(to_owner, key, value):
            adopted += 1
    log.info("adopted %d fact(s) %s -> %s", adopted, from_owner, to_owner)
    return adopted
