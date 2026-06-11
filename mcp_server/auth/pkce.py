"""
PKCE (Proof Key for Code Exchange) helpers — RFC 7636.

Manages the per-login state that must survive the browser redirect roundtrip:
  login  →  Keycloak  →  /auth/callback
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field


# ── PKCE math ────────────────────────────────────────────────────────────────

def generate_pkce_pair() -> tuple[str, str]:
    """
    Returns (code_verifier, code_challenge).

    code_verifier  — random secret, kept server-side
    code_challenge — SHA-256(verifier) base64url-encoded, sent to Keycloak
    """
    verifier = secrets.token_urlsafe(96)          # 128-char URL-safe string
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── In-memory state store (short TTL) ────────────────────────────────────────
# Stores pending auth requests keyed by `state` parameter.
# State ties the callback back to the originating login request.
# TTL: 5 minutes — more than enough for a human to log in.

STATE_TTL = 300  # seconds


@dataclass
class PendingAuth:
    code_verifier: str
    created_at: float = field(default_factory=time.monotonic)


_pending: dict[str, PendingAuth] = {}


def create_state(code_verifier: str) -> str:
    """Store a pending auth entry and return its opaque state token."""
    _evict_expired()
    state = secrets.token_urlsafe(32)
    _pending[state] = PendingAuth(code_verifier=code_verifier)
    return state


def consume_state(state: str) -> str | None:
    """
    Look up and remove a pending auth entry.
    Returns code_verifier if found and not expired, else None.
    """
    _evict_expired()
    entry = _pending.pop(state, None)
    if entry is None:
        return None
    if time.monotonic() - entry.created_at > STATE_TTL:
        return None
    return entry.code_verifier


def _evict_expired() -> None:
    now = time.monotonic()
    stale = [k for k, v in _pending.items() if now - v.created_at > STATE_TTL]
    for k in stale:
        del _pending[k]
