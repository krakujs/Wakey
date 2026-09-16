# SPDX-License-Identifier: Apache-2.0
"""Auth tests (WF-01/WF-12, R-07): token single-use + expiry, session lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wakey.core.storage import SQLiteStorage
from wakey.web.auth import (
    SETUP_TOKEN_TTL_MINUTES,
    AuthManager,
    canonical_token,
    generate_setup_token,
)


def make_manager(tmp_path: Path) -> AuthManager:
    return AuthManager(SQLiteStorage(tmp_path / "wakey.db"))


def test_setup_token_format_is_transcribable() -> None:
    token = generate_setup_token()
    assert token.startswith("WAKE-")
    groups = token.split("-")
    assert len(groups) == 4 and all(len(g) == 4 for g in groups[1:])


def test_token_single_use_and_expires(tmp_path: Path) -> None:
    clock = {"now": datetime.now(UTC)}
    storage = SQLiteStorage(tmp_path / "wakey.db")
    auth = AuthManager(storage, clock=lambda: clock["now"])

    token, created = auth.ensure_setup_token()
    assert created

    # expiry: unused token dies at the TTL boundary
    clock["now"] = clock["now"] + timedelta(minutes=SETUP_TOKEN_TTL_MINUTES + 1)
    assert auth.consume_setup_token(token) is False, "expired token must be rejected"

    token2, _ = auth.ensure_setup_token()  # re-issue after expiry
    clock["now"] = datetime.now(UTC)
    assert auth.consume_setup_token(token2) is True
    assert auth.consume_setup_token(token2) is False, "token must be single-use"
    storage.close()


def test_reissue_invalidates_previous_token(tmp_path: Path) -> None:
    auth = make_manager(tmp_path)
    first, _ = auth.ensure_setup_token()
    second, _ = auth.ensure_setup_token(force=True)  # wakey setup-token path
    assert first != second
    assert auth.consume_setup_token(first) is False, "old token invalidated on reissue"
    assert auth.consume_setup_token(second) is True


def test_session_lifecycle_and_expiry(tmp_path: Path) -> None:
    clock = {"now": datetime.now(UTC)}
    storage = SQLiteStorage(tmp_path / "wakey.db")
    auth = AuthManager(storage, clock=lambda: clock["now"])

    session = auth.start_session()
    assert auth.validate_session(session) is True

    clock["now"] = clock["now"] + timedelta(days=8)
    assert auth.validate_session(session) is False, "session expires after TTL"

    session2 = auth.start_session()
    auth.revoke_session(session2)
    assert auth.validate_session(session2) is False
    storage.close()


def test_sessions_survive_restart(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    auth = AuthManager(storage)
    session = auth.start_session()
    storage.close()

    reopened = AuthManager(SQLiteStorage(tmp_path / "wakey.db"))
    assert reopened.validate_session(session) is True


def test_token_parsing_is_operator_friendly() -> None:
    issued = generate_setup_token()
    mangled = "  " + issued.lower().replace("-", " ") + "  "
    assert canonical_token(mangled) == canonical_token(issued)
    assert canonical_token("no prefix at all").startswith("WAKE")
