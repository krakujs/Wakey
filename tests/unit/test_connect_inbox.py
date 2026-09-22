# SPDX-License-Identifier: Apache-2.0
"""Webhook inbox contract (readiness #2/#6): HMAC gate, relay, fail-closed rules.

The inbox is the only public surface of a Connect deployment. These tests
pin: unsigned traffic never relays, valid deliveries forward with the
runtime identity, oversized bodies die at the edge, and a broker outage
returns a GitHub-retryable 503.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from wakey.forge.connect_inbox import IdentityToken, create_inbox_app

SECRET = "inbox-shared-secret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class FakeIdentity:
    def __init__(self) -> None:
        self.calls = 0

    def get(self) -> str:
        self.calls += 1
        return "identity-token-1"


def make_client(relayed: list[httpx.Request], responses: list[int] | None = None):
    """Build the inbox app against a recording fake broker."""
    statuses = list(responses or [202])
    broker = "https://broker.example/run"

    def handler(request: httpx.Request) -> httpx.Response:
        relayed.append(request)
        return httpx.Response(statuses.pop(0), json={"accepted": True})

    identity = FakeIdentity()
    relay = httpx.Client(timeout=20, transport=httpx.MockTransport(handler))
    app = create_inbox_app(SECRET, broker, identity, relay_client=relay)  # type: ignore[arg-type]
    return TestClient(app), identity, broker


def _signed_request(client: TestClient, body: bytes, **extra: str) -> Any:
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sign(body),
            "X-GitHub-Delivery": "d-1",
            "X-GitHub-Event": "issues",
            **extra,
        },
    )


def test_healthz_is_open_and_information_free() -> None:
    client, _, _ = make_client([])
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").json() == {"status": "ok"}


def test_invalid_signature_never_relays() -> None:
    relayed: list[httpx.Request] = []
    client, identity, _ = make_client(relayed)
    response = client.post(
        "/webhooks/github",
        content=b'{"zen": "x"}',
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64, "X-GitHub-Delivery": "d-2"},
    )
    assert response.status_code == 401
    assert relayed == [] and identity.calls == 0


def test_missing_delivery_header_rejected() -> None:
    relayed: list[httpx.Request] = []
    client, _, _ = make_client(relayed)
    body = b'{"zen": "x"}'
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(body)},
    )
    assert response.status_code == 400
    assert relayed == []


def test_valid_delivery_relays_with_identity_and_event_headers() -> None:
    relayed: list[httpx.Request] = []
    client, _, broker = make_client(relayed)
    body = b'{"action": "opened"}'
    response = _signed_request(client, body)
    assert response.status_code == 202
    assert len(relayed) == 1
    relayed_request = relayed[0]
    assert str(relayed_request.url) == broker + "/webhooks/github"
    assert relayed_request.headers["X-Serverless-Authorization"] == "Bearer identity-token-1"
    assert relayed_request.headers["X-GitHub-Event"] == "issues"
    assert relayed_request.read() == body


def test_broker_server_error_becomes_retryable_503() -> None:
    relayed: list[httpx.Request] = []
    client, _, _ = make_client(relayed, responses=[500])
    response = _signed_request(client, b'{"zen": "x"}')
    assert response.status_code == 503, "GitHub must be told to redeliver"


def test_oversized_body_rejected_before_relay() -> None:
    relayed: list[httpx.Request] = []
    client, _, _ = make_client(relayed)
    big = b"x" * (1024 * 1024 + 1)
    response = client.post(
        "/webhooks/github",
        content=big,
        headers={"X-Hub-Signature-256": sign(big), "X-GitHub-Delivery": "d-3"},
    )
    assert response.status_code == 413
    assert relayed == []


def test_inbox_refuses_to_start_without_a_secret() -> None:
    with pytest.raises(SystemExit):
        create_inbox_app("", "https://broker.example", FakeIdentity())  # type: ignore[arg-type]


def test_identity_token_caches_until_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["Metadata-Flavor"] == "Google"
        return httpx.Response(200, text=f"token-{calls['n']}")

    identity = IdentityToken(
        "https://broker.example", httpx.Client(transport=httpx.MockTransport(handler))
    )
    first = identity.get()
    assert identity.get() == first, "token must be cached across calls"
    assert calls["n"] == 1
    real_time = time.time
    monkeypatch.setattr("wakey.forge.connect_inbox.time.time", lambda: real_time() + 4000)
    assert identity.get() == "token-2", "near expiry the token refreshes"
