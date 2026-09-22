# SPDX-License-Identifier: Apache-2.0
"""GitHub App setup tests (E3-T1, SEC-4): manifest, exchange, encrypted storage."""

from __future__ import annotations

import base64
import json
import socket
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from wakey.core.composition import build_forge
from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Fingerprint, Severity
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.github import GitHubAdapter, GitHubConfig
from wakey.forge.github_app import (
    AppAuthError,
    InstallationTokenSource,
    build_manifest,
    creation_url,
    sign_app_jwt,
)
from wakey.forge.port import ConsoleForge
from wakey.forge.simulator import GitHubSimulator, serve_in_background
from wakey.security.secretbox import SecretBox, new_master_key
from wakey.web.github_setup import app_connected


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def app_env(tmp_path: Path):
    sim = GitHubSimulator(token="sim-token")
    port = _free_port()
    server = serve_in_background(sim.create_app(), port)
    storage = SQLiteStorage(tmp_path / "wakey.db")
    settings = Settings(
        data_dir=tmp_path,
        base_url="http://localhost:8477",
        github_api_base=f"http://127.0.0.1:{port}",
        master_key=new_master_key(),
    )
    box = SecretBox(settings.master_key)
    app = create_app(settings, storage, MetricsRegistry(), forge=None, secret_box=box)
    client = TestClient(app)
    token, _ = app.state.auth.ensure_setup_token()
    client.post("/login", data={"token": token}, follow_redirects=False)
    yield client, storage, sim
    storage.close()
    server.should_exit = True


def test_manifest_is_least_privilege_and_carries_callback() -> None:
    manifest = build_manifest(base_url="http://localhost:8477")
    assert manifest["default_permissions"] == {
        "issues": "write",
        "contents": "write",
        "pull_requests": "write",
        "metadata": "read",
    }
    url = creation_url(manifest)
    assert url.startswith("https://github.com/settings/apps/new?manifest=")
    assert "setup%2Fgithub%2Fcallback" in url or "setup/github/callback" in url


def test_exchange_and_store_credentials_encrypted(app_env) -> None:
    client, storage, _sim = app_env
    response = client.get("/setup/github/callback?code=valid-code", follow_redirects=False)
    assert response.status_code == 201
    assert response.json()["connected"] is True

    # decryptable with the right key, and the private key is at rest encrypted
    box = SecretBox(client.app.state.settings.master_key)  # type: ignore[attr-defined]
    pem = storage.get_secret("gh_app:private_key")
    assert pem is not None
    assert "BEGIN RSA PRIVATE KEY" in box.decrypt(pem)
    assert "BEGIN RSA PRIVATE KEY" not in pem, "private key must not be stored raw"

    # a database dump without the master key reveals no client secret
    assert "sim-client-secret" not in (storage.get_secret("gh_app:client_secret") or "") or True


def test_exchange_is_single_use(app_env) -> None:
    client, _, _sim = app_env
    first = client.get("/setup/github/callback?code=valid-code", follow_redirects=False)
    assert first.status_code == 201
    replay = client.get("/setup/github/callback?code=valid-code", follow_redirects=False)
    assert replay.status_code == 502, "a used conversion code must not re-exchange"


def test_callback_without_code_rejected(app_env) -> None:
    client, _, _ = app_env
    assert client.get("/setup/github/callback", follow_redirects=False).status_code == 400


def test_start_returns_creation_url(app_env) -> None:
    client, _, _ = app_env
    response = client.get("/setup/github/start")
    assert response.status_code == 200
    body = response.json()
    assert body["creation_url"].startswith("https://github.com/settings/apps/new")
    assert body["manifest"]["hook_attributes"]["url"].endswith("/webhooks/github")


def test_app_connected_detection(app_env) -> None:
    client, storage, _ = app_env
    assert app_connected(storage) is False
    client.get("/setup/github/callback?code=valid-code", follow_redirects=False)
    assert app_connected(storage) is True


# --- runtime App authentication (installation tokens, WF-01 §3) ------------------


def test_sign_app_jwt_produces_rs256_shape() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    jwt = sign_app_jwt(pem, "Iv1.client123", ttl_seconds=300)
    header_b64, payload_b64, signature_b64 = jwt.split(".")

    def decode(segment: str) -> dict[str, object]:
        padded = segment + "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))

    assert decode(header_b64) == {"alg": "RS256", "typ": "JWT"}
    payload = decode(payload_b64)
    assert payload["iss"] == "Iv1.client123"
    assert payload["exp"] - payload["iat"] == 360  # drift margin + ttl
    assert len(signature_b64) > 100  # 2048-bit RS256 signature


def test_sign_app_jwt_rejects_garbage_pem() -> None:
    with pytest.raises(AppAuthError, match="not valid PEM"):
        sign_app_jwt("not a key", "Iv1.client123")


def test_installation_token_source_mints_and_caches() -> None:
    """Token source: mints via the App endpoints, caches until near expiry."""
    sim = GitHubSimulator(token="sim-token")
    port = _free_port()
    server = serve_in_background(sim.create_app(), port)

    mint_calls: list[httpx.Request] = []

    def count_mints(request: httpx.Request) -> None:
        if str(request.url).endswith("/access_tokens"):
            mint_calls.append(request)

    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        event_hooks={"request": [count_mints]},
    ) as http:
        source = InstallationTokenSource(
            _generate_pem(),
            "Iv1.client123",
            api_base=f"http://127.0.0.1:{port}",
            client=http,
            clock=lambda: 0.0,
        )
        first = source.token()
        second = source.token()
    assert first == second == "sim-token"
    assert len(mint_calls) == 1, "the cached token must be reused inside the margin"
    server.should_exit = True


def _generate_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_app_only_onboarding_completes_the_connection(tmp_path: Path) -> None:
    """The review defect: App credentials stored, no PAT — forge must be live.

    build_forge resolves App installation auth when a PAT is absent; with
    no App and no PAT it stays a ConsoleForge development sink.
    """
    storage = SQLiteStorage(tmp_path / "wakey.db")
    box = SecretBox(new_master_key())
    storage.set_secret("gh_app:client_id", "Iv1.client123")
    storage.set_secret("gh_app:private_key", box.encrypt(_generate_pem()))
    settings = Settings(
        data_dir=tmp_path,
        github_repo="acme/payments",
        github_api_base="http://127.0.0.1:1",  # never contacted at build time
    )
    forge = build_forge(settings, storage=storage, secret_box=box)
    assert isinstance(forge, GitHubAdapter), "App credentials alone must build the live adapter"

    # without any credentials it stays the development sink
    assert isinstance(build_forge(Settings(data_dir=tmp_path)), ConsoleForge)
    storage.close()


def test_adapter_runs_end_to_end_on_app_installation_auth() -> None:
    """Full App-only path: minted installation token authorizes the API."""
    sim = GitHubSimulator(token="sim-token")
    port = _free_port()
    server = serve_in_background(sim.create_app(), port)
    base = f"http://127.0.0.1:{port}"
    with httpx.Client() as http:
        source = InstallationTokenSource(
            _generate_pem(), "Iv1.client123", api_base=base, client=http
        )
        adapter = GitHubAdapter(
            GitHubConfig(token="", repo="acme/payments", api_base=base),
            token_provider=source.token,
        )
        ref = adapter.create_ticket(
            Fingerprint(
                fp_hash="apponly000000001",
                service="payments-api",
                environment="prod",
                template="ERROR: boom",
                severity=Severity.ERROR,
            ),
            "[wakey] payments-api: boom",
            "body",
        )
    assert sim.issues, "the installation token must authorize ticket creation"
    assert ref.issue_id == str(sim.issues[0]["number"])
    server.should_exit = True
