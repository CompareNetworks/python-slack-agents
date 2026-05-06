"""Signed-state encode/decode for OAuth start URLs.

Produces a URL-safe token containing user_id, server_id, authorize_url, expiry,
and a replay nonce. HMAC-SHA256 over a compact JSON payload.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass


@dataclass
class StatePayload:
    user_id: str
    server_id: str
    authorize_url: str
    exp: int  # unix epoch seconds
    nonce: str = ""  # populated by encode() if blank


class NonceReplayCache:
    """In-memory set of consumed nonces with lazy TTL pruning at access time."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}  # nonce -> expiry

    def claim(self, nonce: str, exp: int) -> bool:
        """Return True if the nonce was unused; False if it has been seen.

        Side effect: marks the nonce as seen with the given expiry, and prunes
        any entries whose expiry has already passed.
        """
        now = int(time.time())
        # Prune expired entries opportunistically.
        for n, e in list(self._seen.items()):
            if e <= now:
                del self._seen[n]
        if nonce in self._seen:
            return False
        self._seen[nonce] = exp
        return True


def _sign(message: bytes, key: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def encode(payload: StatePayload, key: bytes) -> str:
    """Serialize and sign the payload. Returns a URL-safe token."""
    if not payload.nonce:
        payload.nonce = secrets.token_hex(16)
    body = json.dumps(
        {
            "u": payload.user_id,
            "s": payload.server_id,
            "a": payload.authorize_url,
            "e": payload.exp,
            "n": payload.nonce,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    sig = _sign(body, key)
    return base64.urlsafe_b64encode(body + b"." + sig).decode("ascii")


def decode(token: str, key: bytes, cache: NonceReplayCache) -> StatePayload | None:
    """Verify signature, expiry, and nonce-replay. Return None on any failure."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception:
        return None
    # HMAC-SHA256 is always 32 bytes; the encoder inserts a single '.' between
    # body and signature. Split on the fixed signature length rather than
    # searching for '.', because the binary signature itself can contain 0x2e.
    if len(raw) < 33:
        return None
    sig = raw[-32:]
    if raw[-33:-32] != b".":
        return None
    body = raw[:-33]
    expected = _sign(body, key)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    try:
        payload = StatePayload(
            user_id=obj["u"],
            server_id=obj["s"],
            authorize_url=obj["a"],
            exp=int(obj["e"]),
            nonce=obj["n"],
        )
    except (KeyError, TypeError, ValueError):
        return None
    if payload.exp <= int(time.time()):
        return None
    if not cache.claim(payload.nonce, payload.exp):
        return None
    return payload
