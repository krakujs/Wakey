# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the server-rendered live board (WF-12 page 3, NFR-9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Fingerprint, Service, WorkState
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.web.board import render_board_html

FP_HASH = "fp3f9ac2deadbeef"


def make_client(storage: SQLiteStorage, follow_redirects: bool = True) -> TestClient:
    app = create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForge())
    return TestClient(app, follow_redirects=follow_redirects)


def login(client: TestClient) -> None:
    """Consume a fresh setup token so the client holds an admin session (R-07)."""
    token, _ = client.app.state.auth.ensure_setup_token()  # type: ignore[attr-defined]
    response = client.post("/login", data={"token": token}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/board"


def make_fingerprint(state: WorkState = WorkState.OPEN) -> Fingerprint:
    return Fingerprint(
        fp_hash=FP_HASH,
        service="api",
        environment="prod",
        template="KeyError: {key}",
        occurrences=5,
        state=state,
    )


def seed(storage: SQLiteStorage) -> None:
    storage.save_service(Service(name="api", repo="acme/api", ingest_key_hash="abcd1234ef01"))
    storage.save_fingerprint(make_fingerprint())


def test_board_lists_active_fingerprints() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    client = make_client(storage)
    login(client)
    response = client.get("/board", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert FP_HASH in html  # the fingerprint hash column
    assert "api" in html  # the service column
    assert "board" in html  # page title / heading
    assert "wakey board — 1 active" in html


def test_home_never_500s() -> None:
    client = make_client(SQLiteStorage(":memory:"))
    assert client.get("/").status_code in (200, 302, 404)


def test_anonymous_board_redirects_to_login() -> None:
    """R-07 closure: no anonymous operational data access."""
    storage = SQLiteStorage(":memory:")
    seed(storage)
    client = make_client(storage, follow_redirects=False)
    response = client.get("/board")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_with_setup_token_grants_board_access() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    client = make_client(storage)
    token, _ = client.app.state.auth.ensure_setup_token()  # type: ignore[attr-defined]
    response = client.post("/login", data={"token": token}, follow_redirects=False)
    assert response.status_code == 302
    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
    assert client.get("/board", follow_redirects=False).status_code == 200


def test_bad_token_rejected() -> None:
    client = make_client(SQLiteStorage(":memory:"))
    client.app.state.auth.ensure_setup_token()  # type: ignore[attr-defined]
    response = client.post("/login", data={"token": "WAKE-XXXX-YYYY-ZZZZ"})
    assert response.status_code == 401


def test_render_board_html_empty_renders_zero_active() -> None:
    html = render_board_html([], generated_at="2026-09-16T00:00:00+00:00")
    assert "wakey board — 0 active" in html
    assert "wakey — board" in html
    assert "<!doctype html>" in html


def test_render_board_html_colors_states() -> None:
    # amber while the system acts, green once a fix is verified (design.md).
    rows: list[Any] = [
        make_fingerprint(state=WorkState.INVESTIGATING),
        make_fingerprint(state=WorkState.FIXING),
        make_fingerprint(state=WorkState.VERIFIED_CLOSED),
        make_fingerprint(state=WorkState.NEW),
    ]
    html = render_board_html(rows, generated_at="2026-09-16T00:00:00+00:00")
    # count only the state-cell spans, not palette literals in the CSS block
    assert html.count('color:#ffb020">') == 2  # investigating + fixing
    assert "#2fd67b" in html  # verified-closed
    assert "wakey board — 4 active" in html


def test_root_redirects_to_board(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    client = TestClient(create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForge()))
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/board"


def test_settings_page_renders_without_secrets(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    app = create_app(
        Settings(llm_api_key="secret"), storage, MetricsRegistry(), forge=ConsoleForge()
    )
    client = TestClient(app)
    token, _ = app.state.auth.ensure_setup_token()
    client.post("/login", data={"token": token}, follow_redirects=False)
    response = client.get("/settings")
    assert response.status_code == 200
    assert "configured" in response.text
    assert "secret" not in response.text
