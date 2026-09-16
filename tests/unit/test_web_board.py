# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the server-rendered live board (WF-12 page 3, NFR-9)."""

from __future__ import annotations

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


def make_client(storage: SQLiteStorage) -> TestClient:
    return TestClient(create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForge()))


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
    response = make_client(storage).get("/board")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert FP_HASH in html  # the fingerprint hash column
    assert "api" in html  # the service column
    assert "board" in html  # page title / heading
    assert "wakey board — 1 active" in html


def test_home_never_500s() -> None:
    client = make_client(SQLiteStorage(":memory:"))
    assert client.get("/").status_code in (200, 404)


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
    assert html.count("#ffb020") == 2  # investigating + fixing
    assert "#2fd67b" in html  # verified-closed
    assert "wakey board — 4 active" in html


def test_root_redirects_to_board(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    client = TestClient(create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForge()))
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/board"
