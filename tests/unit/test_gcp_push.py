# SPDX-License-Identifier: Apache-2.0
"""GCP Pub/Sub push route tests (WF-02 §3, E4-T4): OIDC gate + dedup by messageId."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Service
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.ingest.oidc import OidcVerificationError
from wakey.ingest.pipeline import IngestPipeline
from wakey.ingest.worker import DeliveryWorker
from wakey.security.redaction import RedactionEngine

KEY = "wk_gcp_push_key"


def envelope(msg_id: str = "m-1", text: str = "ERROR: gcp error line") -> str:
    return json.dumps(
        {
            "message": {
                "data": base64.b64encode(
                    json.dumps({"level": "error", "msg": text}).encode()
                ).decode(),
                "messageId": msg_id,
            },
            "subscription": "projects/p/subscriptions/s",
        }
    )


@pytest.fixture()
def env(tmp_path: Path):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(Service(name="gcp-svc", repo="acme/gcp", ingest_key_hash=hashlib_key(KEY)))
    forge = ConsoleForge()
    app = create_app(Settings(), storage, MetricsRegistry(), forge=forge)
    client = TestClient(app)
    worker = DeliveryWorker(storage, IngestPipeline(storage, forge), RedactionEngine())
    return client, storage, forge, worker


def hashlib_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def drain(worker: DeliveryWorker) -> None:
    while worker.process_next():
        pass


def test_push_without_oidc_config_uses_key_auth_and_processes(env) -> None:
    client, _, forge, worker = env
    response = client.post(
        f"/ingest/gcp/{KEY}", content=envelope(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 202
    drain(worker)
    assert len(forge.created) == 0  # 1 occurrence: recorded, below threshold

    client.post(f"/ingest/gcp/{KEY}", content=envelope())
    drain(worker)
    assert len(forge.created) == 0


def test_message_id_deduplicates_push_retries(env) -> None:
    """Pub/Sub retries the same push on the same messageId — dedup must hold."""
    client, _, forge, worker = env
    texts = [f"ERROR: connection refused to db (attempt {i})" for i in range(3)]
    for i, text in enumerate(texts):
        response = client.post(f"/ingest/gcp/{KEY}", content=envelope(msg_id=f"m-{i}", text=text))
        assert response.status_code == 202
    # a push retry carries the SAME messageId and must deduplicate
    retry = client.post(f"/ingest/gcp/{KEY}", content=envelope(msg_id="m-0", text=texts[0]))
    assert retry.status_code == 200
    assert retry.json() == {"deduplicated": True}
    drain(worker)
    assert len(forge.created) == 1, "retried pushes must not duplicate tickets"


def test_malformed_envelope_dead_lettered_not_crashing(env) -> None:
    client, storage, _, worker = env
    response = client.post(f"/ingest/gcp/{KEY}", content="{not-json")
    assert response.status_code == 400
    drain(worker)
    assert storage.pending_delivery_count() == 0


def test_oidc_audience_configured_rejects_missing_token(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(Service(name="gcp-svc", repo="acme/gcp", ingest_key_hash=hashlib_key(KEY)))
    app = create_app(
        Settings(oidc_audience="wakey-push"), storage, MetricsRegistry(), forge=ConsoleForge()
    )
    response = TestClient(app).post(f"/ingest/gcp/{KEY}", content=envelope())
    assert response.status_code == 401
    assert "OIDC" in response.json()["error"]


def test_oidc_verification_failure_blocks_ingest(tmp_path: Path, monkeypatch) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(Service(name="gcp-svc", repo="acme/gcp", ingest_key_hash=hashlib_key(KEY)))
    app = create_app(
        Settings(oidc_audience="wakey-push"), storage, MetricsRegistry(), forge=ConsoleForge()
    )

    class RejectingVerifier:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def verify(self, token: str) -> dict:
            raise OidcVerificationError("signature verification failed")

    monkeypatch.setattr("wakey.core.server.JwtVerifier", RejectingVerifier)
    response = TestClient(app).post(
        f"/ingest/gcp/{KEY}",
        content=envelope(),
        headers={"Authorization": "Bearer faketoken"},
    )
    assert response.status_code == 401
    assert storage.pending_delivery_count() == 0, "rejected pushes must not persist"


def test_oidc_verified_push_is_accepted_and_processed(tmp_path: Path, monkeypatch) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(Service(name="gcp-svc", repo="acme/gcp", ingest_key_hash=hashlib_key(KEY)))
    forge = ConsoleForge()
    app = create_app(Settings(oidc_audience="wakey-push"), storage, MetricsRegistry(), forge=forge)
    client = TestClient(app)
    worker = DeliveryWorker(storage, IngestPipeline(storage, forge), RedactionEngine())

    seen_audiences: list[str] = []

    class AcceptingVerifier:
        def __init__(self, audience: str, **kwargs: object) -> None:
            seen_audiences.append(audience)

        def verify(self, token: str) -> dict:
            return {"iss": "https://accounts.google.com", "aud": "wakey-push"}

    monkeypatch.setattr("wakey.core.server.JwtVerifier", AcceptingVerifier)

    burst = "\n".join([envelope(text="ERROR: gcp verified flow")] * 1)
    response = client.post(
        f"/ingest/gcp/{KEY}",
        content=burst,
        headers={"Authorization": "Bearer goodtoken"},
    )
    assert response.status_code == 202
    while worker.process_next():
        pass
    assert seen_audiences == ["wakey-push"], "audience must reach the verifier"
    assert storage.pending_delivery_count() == 0
