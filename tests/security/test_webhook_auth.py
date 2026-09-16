# SPDX-License-Identifier: Apache-2.0
"""Tests: HMAC webhook signature verification (WF-02 §2, migration v2)."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Service
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage

KEY = "wk_signed_key"
SECRET = "hook-secret-123"
PAYLOAD = '{"level": "error", "message": "boom"}'


def sign(secret: str, body: str) -> str:
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture()
def signed_client(tmp_path: Path) -> TestClient:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        Service(
            name="signed-api",
            repo="acme/signed",
            ingest_key_hash=hashlib.sha256(KEY.encode()).hexdigest()[:16],
            webhook_secret=SECRET,
        )
    )
    return TestClient(create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForgeStub()))


class ConsoleForgeStub:
    """Minimal ForgePort so the ingest route exists (no tickets asserted here)."""

    class _Ref:
        issue_id = "console-1"
        url = "console://tickets/1"

    def create_ticket(self, fingerprint: object, title: str, body: str) -> object:
        return self._Ref()

    def add_comment(self, ticket: object, body: str) -> None:
        pass


def test_valid_signature_accepted(signed_client: TestClient) -> None:
    response = signed_client.post(
        f"/ingest/{KEY}",
        content=PAYLOAD,
        headers={"X-Wakey-Signature": sign(SECRET, PAYLOAD)},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True
    assert response.json()["state"] == "pending"


def test_invalid_signature_rejected(signed_client: TestClient) -> None:
    response = signed_client.post(
        f"/ingest/{KEY}",
        content=PAYLOAD,
        headers={"X-Wakey-Signature": "sha256=" + "0" * 64},
    )
    assert response.status_code == 401


def test_missing_signature_rejected_when_secret_configured(signed_client: TestClient) -> None:
    response = signed_client.post(f"/ingest/{KEY}", content=PAYLOAD)
    assert response.status_code == 401


def test_signature_bound_to_body(signed_client: TestClient) -> None:
    # signed for a different body must not validate for this one
    other = sign(SECRET, '{"level": "error", "message": "different"}')
    response = signed_client.post(
        f"/ingest/{KEY}", content=PAYLOAD, headers={"X-Wakey-Signature": other}
    )
    assert response.status_code == 401
