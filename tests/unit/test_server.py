# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the server skeleton (E2-T4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage


class BrokenStorage(SQLiteStorage):
    """Storage whose readiness probe always fails (dependency-down simulation)."""

    def __init__(self) -> None:
        pass  # no backend needed: only check_ready is consulted

    def check_ready(self) -> bool:
        return False


@pytest.fixture()
def storage(tmp_path: Path) -> SQLiteStorage:
    return SQLiteStorage(tmp_path / "wakey.db")


def make_client(storage: SQLiteStorage) -> TestClient:
    return TestClient(create_app(Settings(), storage, MetricsRegistry()))


def test_healthz_is_always_ok(storage: SQLiteStorage) -> None:
    response = make_client(storage).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reflects_storage(storage: SQLiteStorage) -> None:
    assert make_client(storage).get("/readyz").status_code == 200
    response = make_client(BrokenStorage()).get("/readyz")
    assert response.status_code == 503


def test_request_id_echoed_and_generated(storage: SQLiteStorage) -> None:
    client = make_client(storage)
    generated = client.get("/healthz")
    assert generated.headers["X-Request-ID"]

    echoed = client.get("/healthz", headers={"X-Request-ID": "my-id-123"})
    assert echoed.headers["X-Request-ID"] == "my-id-123"


def test_metrics_render_counters(storage: SQLiteStorage) -> None:
    client = make_client(storage)
    body = client.get("/metrics").text
    assert "# TYPE wakey_up counter" in body
    assert "wakey_up 1" in body
