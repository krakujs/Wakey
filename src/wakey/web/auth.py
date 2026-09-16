# SPDX-License-Identifier: Apache-2.0
"""Dashboard authentication (WF-01 §2 bootstrap admin, WF-12 §Auth, R-07).

Two credentials:

- **Setup token** — single-use, 30-minute expiry, one active at a time;
  printed by the server on first start (and regenerable via
  ``wakey setup-token``). Consumed on first login to create the admin
  session. This is what locks a fresh install.
- **Admin session** — opaque random id persisted server-side, delivered
  as an HttpOnly / SameSite=Lax cookie (``Secure`` added when the
  configured BASE_URL is HTTPS). Sessions survive restarts; logout
  revokes.

Health/readiness/metrics and ingest/webhooks are intentionally public
(aggregate counters only, keys verified per request); every operational
page/API requires a valid session.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from wakey.core.models import utcnow
from wakey.core.storage import Storage
from wakey.security.secretbox import hash_token

SETUP_TOKEN_PREFIX = "WAKE-"
SETUP_TOKEN_TTL_MINUTES = 30
SESSION_TTL_DAYS = 7
SESSION_COOKIE = "wakey_session"
# Unambiguous alphabet: no 0/O, 1/I/L confusions when transcribed by hand.
_TOKEN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_SETUP_TOKEN_KEY = "setup_token"
_SETUP_TOKEN_AT_KEY = "setup_token_issued_at"
_SESSIONS_KEY = "admin_sessions"


def generate_setup_token(groups: int = 3, group_len: int = 4) -> str:
    body = "-".join(
        "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(group_len)) for _ in range(groups)
    )
    return f"{SETUP_TOKEN_PREFIX}{body}"


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def canonical_token(value: str) -> str:
    """Normalize operator input: case-insensitive, separator-tolerant.

    Both issued and typed tokens compare in canonical form
    (``WAKE-`` prefix + body, all other separators removed).
    """
    compact = value.strip().upper().replace(" ", "").replace("_", "").replace("-", "")
    if not compact.startswith(SETUP_TOKEN_PREFIX.replace("-", "")):
        compact = SETUP_TOKEN_PREFIX.replace("-", "") + compact
    return compact


class AuthManager:
    """Setup-token bootstrap + admin sessions over the secrets store."""

    def __init__(self, storage: Storage, clock: Callable[[], datetime] = utcnow) -> None:
        self._storage = storage
        self._clock = clock

    # --- setup token ------------------------------------------------------------

    def ensure_setup_token(self, force: bool = False) -> tuple[str, bool]:
        """Return (token, newly_created). One active token at a time.

        ``force=True`` re-issues even when a valid token exists — the old
        one is destroyed (``wakey setup-token``; WF-12 lost-token path).
        Only the SHA-256 of the token is stored, so a database dump cannot
        be replayed to unlock the dashboard (SEC-4).
        """
        stored = self._storage.get_secret(_SETUP_TOKEN_KEY)
        if not force and stored and not self._setup_token_expired():
            return stored, False  # stored value IS the token hash; echo impossible
        token = generate_setup_token()
        self._storage.set_secret(_SETUP_TOKEN_KEY, hash_token(canonical_token(token)))
        self._storage.set_secret(_SETUP_TOKEN_AT_KEY, self._clock().isoformat())
        return token, True

    def _setup_token_expired(self) -> bool:
        issued = self._storage.get_secret(_SETUP_TOKEN_AT_KEY)
        if not issued:
            return True
        issued_at = datetime.fromisoformat(issued)
        age = self._clock() - issued_at
        return age > timedelta(minutes=SETUP_TOKEN_TTL_MINUTES)

    def consume_setup_token(self, token: str) -> bool:
        """Single-use: valid, unexpired token becomes a session and is destroyed."""
        expected_hash = self._storage.get_secret(_SETUP_TOKEN_KEY)
        if not expected_hash or self._setup_token_expired():
            return False
        if not secrets.compare_digest(hash_token(canonical_token(token)), expected_hash):
            return False
        self._storage.delete_secret(_SETUP_TOKEN_KEY)
        self._storage.delete_secret(_SETUP_TOKEN_AT_KEY)
        return True

    # --- sessions -----------------------------------------------------------------

    def _sessions(self) -> dict[str, str]:
        """Keyed by SHA-256(session id) — the raw id never touches storage."""
        raw = self._storage.get_secret(_SESSIONS_KEY)
        if not raw:
            return {}
        try:
            return {k: str(v) for k, v in json.loads(raw).items()}
        except (json.JSONDecodeError, AttributeError):
            return {}

    def _write_sessions(self, sessions: dict[str, str]) -> None:
        self._storage.set_secret(_SESSIONS_KEY, json.dumps(sessions))

    def _prune_expired(self, sessions: dict[str, str]) -> dict[str, str]:
        now = self._clock()
        alive: dict[str, str] = {}
        for sid, expiry in sessions.items():
            try:
                if datetime.fromisoformat(expiry) > now:
                    alive[sid] = expiry
            except ValueError:
                continue  # malformed expiry: drop
        return alive

    def start_session(self) -> str:
        sessions = self._prune_expired(self._sessions())
        session_id = generate_session_id()
        expiry = (self._clock() + timedelta(days=SESSION_TTL_DAYS)).isoformat()
        sessions[hash_token(session_id)] = expiry
        self._write_sessions(sessions)
        return session_id

    def validate_session(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        sessions = self._prune_expired(self._sessions())
        self._write_sessions(sessions)
        return hash_token(session_id) in sessions

    def revoke_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        sessions = self._sessions()
        key = hash_token(session_id)
        if key in sessions:
            del sessions[key]
            self._write_sessions(sessions)
