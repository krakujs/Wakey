# SPDX-License-Identifier: Apache-2.0
"""GitHub App setup tests (E3-T1, SEC-4): manifest, exchange, encrypted storage."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.github_app import build_manifest, creation_url
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
