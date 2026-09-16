# SPDX-License-Identifier: Apache-2.0
"""End-to-end ingest tests: key auth → parse → redact → fingerprint → policy → ticket."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Service
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge

KEY = "wk_demo_service_key"


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        Service(name="payments-api", repo="acme/payments", ingest_key_hash=key_hash(KEY))
    )
    forge = ConsoleForge()
    app = create_app(Settings(), storage, MetricsRegistry(), forge=forge)
    app.state.forge = forge
    return TestClient(app)


def test_unknown_key_rejected(client: TestClient) -> None:
    response = client.post("/ingest/wrong-key", content='{"msg": "boom"}')
    assert response.status_code == 401


def test_error_burst_creates_exactly_one_ticket(client: TestClient) -> None:
    burst = "\n".join(["ERROR: connection refused to db"] * 3)
    response = client.post(f"/ingest/{KEY}", content=burst)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 3
    assert body["outcomes"].get("ticketed") == 1

    # in-flight suppression: more of the same never re-tickets (DET-14)
    more = client.post(f"/ingest/{KEY}", content="ERROR: connection refused to db")
    outcomes = more.json()["outcomes"]
    assert "ticketed" not in outcomes
    forge: ConsoleForge = client.app.state.forge  # type: ignore[attr-defined]
    assert len(forge.created) == 1


def test_secrets_never_reach_the_ticket_body(client: TestClient) -> None:
    line = "ERROR: auth failed for AKIAIOSFODNN7EXAMPLE at checkout"
    client.post(f"/ingest/{KEY}", content="\n".join([line] * 3))  # reach the threshold
    forge: ConsoleForge = client.app.state.forge  # type: ignore[attr-defined]
    assert forge.created, "ticket expected for auth failure error"
    _, body = forge.created[-1]
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "[REDACTED:aws_access_key]" in body


def test_broken_json_counted_as_dead_letter(client: TestClient) -> None:
    response = client.post(f"/ingest/{KEY}", content='{"level": "error", "msg": "trunc')
    body = response.json()
    assert body["accepted"] == 0
    assert body["dead_lettered"] == 1
