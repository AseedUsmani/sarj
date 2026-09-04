"""Accounts, optional.

The assistant works without an account: an anonymous browser gets a session id
in localStorage and its facts are keyed by that. Signing in does not start a new
conversation — it *adopts* the anonymous session, so whatever Sarjy already
learned carries over and then follows the account to other devices.

That is the whole point of the feature, and it shapes the storage: `facts` is
keyed by an **owner**, which is either a raw session id or `user:<id>`. Signing
in migrates one to the other. There is no separate per-user table to keep in
step.

Implementation notes:

- Passwords are hashed with `hashlib.scrypt` from the standard library. No new
  dependency, and it is memory-hard, which plain sha256 is not.
- Tokens are HMAC-signed rather than JWTs: same guarantee here, no library, and
  nothing about the payload needs to be standard.
- Tokens are bearer credentials, so this is only safe over HTTPS. Render
  terminates TLS; locally it is http and that is a development compromise.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from app import db
from app.config import settings

log = logging.getLogger("sarjy.auth")

TOKEN_TTL_SECONDS = 30 * 24 * 3600          # 30 days; this is a weather assistant
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)  # ~50ms per hash on a small instance

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


class AuthError(RuntimeError):
    """Message is safe to show a user; it never says which half was wrong."""


@dataclass(frozen=True)
class User:
    id: int
    email: str

    @property
    def owner(self) -> str:
        return owner_for_user(self.id)


def owner_for_user(user_id: int) -> str:
    return f"user:{user_id}"


# ----------------------------------------------------------------- hashing --
def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_hash(password, salt).hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        # compare_digest: constant time, so a wrong password cannot be found by
        # timing how long the comparison took.
        return hmac.compare_digest(
            _hash(password, bytes.fromhex(salt_hex)), bytes.fromhex(digest_hex))
    except (ValueError, AttributeError):
        return False


# ------------------------------------------------------------------ tokens --
def _secret() -> bytes:
    return settings.auth_secret.encode()


def mint_token(user_id: int) -> str:
    payload = json.dumps({"uid": user_id, "exp": int(time.time()) + TOKEN_TTL_SECONDS},
                         separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=")
    sig = base64.urlsafe_b64encode(
        hmac.new(_secret(), body, hashlib.sha256).digest()).rstrip(b"=")
    return f"{body.decode()}.{sig.decode()}"


def read_token(token: str) -> Optional[int]:
    """Returns the user id, or None. Never raises: a malformed token from a
    client is an anonymous request, not a server error."""
    try:
        body, sig = token.split(".")
        expected = base64.urlsafe_b64encode(
            hmac.new(_secret(), body.encode(), hashlib.sha256).digest()).rstrip(b"=")
        if not hmac.compare_digest(sig.encode(), expected):
            return None
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        if payload.get("exp", 0) < time.time():
            return None
        return int(payload["uid"])
    except Exception:
        return None


# ------------------------------------------------------------------- store --
_INSERT_USER = text(
    "INSERT INTO users (email, password_hash, created_at) "
    "VALUES (:e, :p, CURRENT_TIMESTAMP)")
_BY_EMAIL = text("SELECT id, email, password_hash FROM users WHERE email = :e")
_BY_ID = text("SELECT id, email FROM users WHERE id = :i")


def _normalise(email: str) -> str:
    return (email or "").strip().lower()


async def register(email: str, password: str) -> tuple[User, str]:
    email = _normalise(email)
    if not EMAIL_RE.match(email):
        raise AuthError("That doesn't look like an email address.")
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Password must be at least {MIN_PASSWORD} characters.")

    async with db.engine().begin() as conn:
        existing = (await conn.execute(_BY_EMAIL, {"e": email})).fetchone()
        if existing:
            raise AuthError("An account with that email already exists.")
        await conn.execute(_INSERT_USER, {"e": email, "p": hash_password(password)})
        row = (await conn.execute(_BY_EMAIL, {"e": email})).fetchone()

    user = User(id=row[0], email=row[1])
    log.info("registered user=%s", user.id)
    return user, mint_token(user.id)


async def login(email: str, password: str) -> tuple[User, str]:
    email = _normalise(email)
    async with db.engine().connect() as conn:
        row = (await conn.execute(_BY_EMAIL, {"e": email})).fetchone()

    # One message for both cases: saying "no such account" tells an attacker
    # which emails are registered.
    if not row or not verify_password(password, row[2]):
        raise AuthError("Email or password is incorrect.")

    user = User(id=row[0], email=row[1])
    return user, mint_token(user.id)


async def user_by_id(user_id: int) -> Optional[User]:
    async with db.engine().connect() as conn:
        row = (await conn.execute(_BY_ID, {"i": user_id})).fetchone()
    return User(id=row[0], email=row[1]) if row else None


async def resolve(authorization: Optional[str]) -> Optional[User]:
    """Bearer header -> user, or None for an anonymous request."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    user_id = read_token(authorization[7:].strip())
    return await user_by_id(user_id) if user_id else None
