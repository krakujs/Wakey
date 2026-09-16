# SPDX-License-Identifier: Apache-2.0
"""Service registration tests (WF-01 §5, E3-T5): one-time keys, rotation, auth."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge


@pytest.fixture()
def client(tmp_path: Path):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    app = create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForge())
    c = TestClient(app)
    token, _ = app.state.auth.ensure_setup_token()
    c.post("/login", data={"token": token}, follow_redirects=False)
    return c, storage


def test_register_returns_working_key_exactly_once(client) -> None:
    c, storage = client
    response = c.post("/api/services", json={"name": "payments-api", "repo": "acme/payments"})
    assert response.status_code == 201
    key = response.json()["ingest_key"]
    assert key.startswith("wk_")

    # the key works immediately over the real ingest route
    ingest = c.post(f"/ingest/{key}", content="ERROR: first real error")
    assert ingest.status_code == 202

    # the stored value is only the hash
    stored = storage.get_service("payments-api")
    assert stored is not None
    assert stored.ingest_key_hash == hashlib.sha256(key.encode()).hexdigest()[:16]
    listed = c.get("/api/services").json()["services"]
    assert all("ingest_key" not in s for s in listed)


def test_rotation_invalidates_the_old_key(client) -> None:
    c, storage = client
    key = c.post("/api/services", json={"name": "svc", "repo": "acme/s"}).json()["ingest_key"]
    new_key = c.post("/api/services/svc/rotate").json()["ingest_key"]
    assert new_key != key

    replay = c.post(f"/ingest/{key}", content="ERROR: boom")
    assert replay.status_code == 401, "old key must stop working immediately"
    fresh = c.post(f"/ingest/{new_key}", content="ERROR: boom")
    assert fresh.status_code == 202


def test_duplicate_and_bad_repo_rejected(client) -> None:
    c, _ = client
    first = c.post("/api/services", json={"name": "svc", "repo": "acme/s"})
    assert first.status_code == 201
    dup = c.post("/api/services", json={"name": "svc", "repo": "acme/s"})
    assert dup.status_code == 400
    bad_repo = c.post("/api/services", json={"name": "svc2", "repo": "not-a-repo"})
    assert bad_repo.status_code == 400


def test_anonymous_registration_refused(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    app = create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForge())
    response = TestClient(app).post("/api/services", json={"name": "svc", "repo": "acme/s"})
    assert response.status_code == 401, "registration is an admin operation (R-07)"


def test_wizard_page_renders_and_shows_key_once(client) -> None:
    c, _ = client
    page = c.get("/setup")
    assert page.status_code == 200

    response = c.post(
        "/setup",
        data={"name": "wizard-svc", "repo": "acme/wizard"},
        follow_redirects=False,
    )
    body = response.text
    assert "never shown again" in body
    assert "wk_" in body
    assert "wk_" not in c.get("/setup").text, "key must not persist on subsequent pages"


def test_config_endpoint_updates_stored_config(client) -> None:
    c, storage = client
    c.post("/api/services", json={"name": "svc", "repo": "acme/s"})
    response = c.patch(
        "/api/services/svc/config",
        json={
            "autonomy": "fix",
            "test_command": "python -m pytest tests -q",
            "confidence_floor": 0.5,
        },
    )
    assert response.status_code == 200
    stored = storage.get_service("svc")
    assert stored is not None
    assert '"autonomy":"fix"' in stored.config_json.replace(" ", "")
    assert "python -m pytest tests -q" in stored.config_json


def test_config_endpoint_rejects_unknown_keys(client) -> None:
    c, _ = client
    c.post("/api/services", json={"name": "svc", "repo": "acme/s"})
    response = c.patch("/api/services/svc/config", json={"autnomy": "fix"})
    assert response.status_code == 400
    assert response.json()["errors"]
