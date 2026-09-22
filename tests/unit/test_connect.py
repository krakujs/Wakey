# SPDX-License-Identifier: Apache-2.0
"""Adversarial hosted pairing, tenant isolation, relay and local activation tests."""

import hashlib
import hmac
import json
import os
import re
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from wakey.__main__ import load_dotenv
from wakey.connect import main as connect_main
from wakey.core.composition import compose
from wakey.core.config import Settings
from wakey.core.models import LogEvent, Service, Severity, utcnow
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.connect_client import ConnectClient, ConnectForge
from wakey.forge.connect_server import create_connect_app
from wakey.forge.connect_store import ConnectDenied, ConnectStore
from wakey.forge.github import ForgeError
from wakey.ingest.pipeline import IngestPipeline, Outcome
from wakey.security.bootstrap import persistent_master_key
from wakey.security.secretbox import SecretBox
from wakey.web.connect import render_connect


class GitHub:
    def __init__(self):
        self.reply = {"access_token": "synthetic-user-token"}
        self.polls = 0
        self.minted = []

    def device(self):
        return {"device_code": "synthetic-device-secret", "user_code": "ABCD-EFGH", "interval": 5}

    def poll(self, code):
        assert code == "synthetic-device-secret"
        self.polls += 1
        return self.reply

    def repositories(self, token):
        assert token == "synthetic-user-token"
        return 7, [
            {"id": 11, "name": "synthetic/one", "installation": 91},
            {"id": 22, "name": "synthetic/two", "installation": 92},
        ]

    def token(self, repo):
        self.minted.append(repo)
        return {"token": f"synthetic-token-{repo['id']}", "expires_at": "2035-01-01T00:00:00Z"}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    box = SecretBox("a1" * 32)
    store = ConnectStore(tmp_path / "connect.db", box)
    github = GitHub()
    app = create_connect_app(
        store, github, app_slug="synthetic-wakey", webhook_secret="synthetic-hook"
    )
    with TestClient(app) as api:
        yield api, store, github, clock, box
    store.close()


def paired(harness, repo=11):
    api, _, _, clock, _ = harness
    data = api.post("/v1/pair").json()
    headers = {"Authorization": "Bearer " + data["credential"]}
    clock[0] += 6
    assert api.post("/v1/poll", headers=headers).json()["state"] == "selecting"
    assert api.post("/v1/select", headers=headers, json={"repository_id": repo}).status_code == 200
    return headers


def deliver(api, kind, event, delivery="event-1"):
    raw = json.dumps(event).encode()
    headers = {
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": kind,
        "X-Hub-Signature-256": "sha256="
        + hmac.new(b"synthetic-hook", raw, hashlib.sha256).hexdigest(),
    }
    return api.post("/webhooks/github", content=raw, headers=headers)


def test_pairing_scoped_token_and_disconnect(harness):
    api, store, github, _, _ = harness
    headers = paired(harness)
    assert api.get("/healthz").json() == {"status": "ok"}
    response = api.post("/v1/token", headers=headers)
    assert response.json()["token"] == "synthetic-token-11"
    assert response.headers["cache-control"] == "no-store"
    assert github.minted[-1] == {"id": 11, "name": "synthetic/one", "installation": 91}
    dump = " ".join(store.db.iterdump())
    assert "synthetic-device-secret" not in dump
    assert "synthetic/one" not in dump
    assert headers["Authorization"][7:] not in dump
    assert api.post("/v1/disconnect", headers=headers).status_code == 200
    assert api.post("/v1/token", headers=headers).status_code == 403


@pytest.mark.parametrize("path", ["/v1/poll", "/v1/token", "/v1/disconnect"])
def test_missing_or_forged_deployment_credentials(harness, path):
    api = harness[0]
    assert api.post(path).status_code == 403
    assert api.post(path, headers={"Authorization": "Bearer unknown"}).status_code == 403


def test_pending_expiry_and_poll_interval(harness):
    api, _, github, clock, _ = harness
    data = api.post("/v1/pair").json()
    headers = {"Authorization": "Bearer " + data["credential"]}
    assert api.post("/v1/token", headers=headers).status_code == 403
    assert api.get("/v1/events", headers=headers).status_code == 403
    assert api.post("/v1/select", headers=headers, json={"repository_id": 11}).status_code == 403
    assert api.post("/v1/poll", headers=headers).json()["state"] == "pending"
    assert github.polls == 0
    github.reply = {"error": "slow_down"}
    clock[0] += 6
    assert api.post("/v1/poll", headers=headers).json()["interval"] == 10
    github.reply = {"error": "authorization_pending"}
    clock[0] += 11
    assert api.post("/v1/poll", headers=headers).json()["state"] == "pending"
    clock[0] += 1000
    assert api.post("/v1/poll", headers=headers).status_code == 403


def test_github_denial_and_unknown_repository(harness):
    api, _, github, clock, _ = harness
    data = api.post("/v1/pair").json()
    headers = {"Authorization": "Bearer " + data["credential"]}
    github.reply = {"error": "access_denied"}
    clock[0] += 6
    assert api.post("/v1/poll", headers=headers).status_code == 403
    github.reply = {"access_token": "synthetic-user-token"}
    data = api.post("/v1/pair").json()
    headers = {"Authorization": "Bearer " + data["credential"]}
    clock[0] += 6
    api.post("/v1/poll", headers=headers)
    assert api.post("/v1/select", headers=headers, json={"repository_id": 999}).status_code == 403
    assert github.minted == []


def test_relay_never_crosses_installation_or_repository(harness):
    api = harness[0]
    one, two = paired(harness, 11), paired(harness, 22)
    event = {
        "installation": {"id": 91},
        "repository": {"id": 11, "full_name": "synthetic/one"},
        "action": "created",
        "comment": {"body": "contact someone@example.com"},
    }
    assert deliver(api, "issue_comment", event).status_code == 202
    assert deliver(api, "issue_comment", event).status_code == 202
    events = api.get("/v1/events", headers=one).json()["events"]
    assert len(events) == 1 and "someone@example.com" not in json.dumps(events)
    assert api.get("/v1/events", headers=two).json() == {"events": []}
    api.post("/v1/ack", headers=two, json={"delivery_id": "event-1"})
    assert len(api.get("/v1/events", headers=one).json()["events"]) == 1
    api.post("/v1/ack", headers=one, json={"delivery_id": "event-1"})
    deliver(api, "issue_comment", event)
    assert api.get("/v1/events", headers=one).json() == {"events": []}


@pytest.mark.parametrize(
    "kind,event",
    [
        ("installation", {"action": "deleted", "installation": {"id": 91}}),
        ("installation", {"action": "suspend", "installation": {"id": 91}}),
        (
            "installation_repositories",
            {"installation": {"id": 91}, "repositories_removed": [{"id": 11}]},
        ),
        ("github_app_authorization", {"action": "revoked", "sender": {"id": 7}}),
    ],
)
def test_github_revocation_disables_credentials(harness, kind, event):
    headers = paired(harness)
    assert deliver(harness[0], kind, event).status_code == 202
    assert harness[0].post("/v1/token", headers=headers).status_code == 403


def test_webhook_rejects_untrusted_or_oversized_input(harness):
    api = harness[0]
    assert api.post("/webhooks/github", content=b"{}").status_code == 401
    assert api.post("/webhooks/github", content=b"x" * (1024 * 1024 + 1)).status_code == 413
    assert deliver(api, "ping", [], delivery="e").status_code == 400
    assert deliver(api, "ping", {}, delivery="").status_code == 400


def test_broker_restart_and_connection_claim(harness, tmp_path):
    api, store, _, _, box = harness
    headers = paired(harness)
    reopened = ConnectStore(tmp_path / "connect.db", box)
    record = reopened.read(headers["Authorization"][7:])
    assert record["state"] == "connected"
    with pytest.raises(ConnectDenied, match="changed"):
        reopened.write({**record, "state": "selecting"}, "connected", {})
    reopened.close()
    assert store.connected()[0]["data"]["repository"]["id"] == 11


def test_local_client_activation_and_durable_relay(harness, tmp_path):
    api, _, _, clock, box = harness
    db = SQLiteStorage(tmp_path / "local.db", secret_box=box)
    connect = ConnectClient("https://connect.test", db, box, http=api)
    with pytest.raises(ForgeError, match="not ready"):
        connect.token()
    connect.receive()  # unpaired: idle, no outbound request
    connect.begin()
    clock[0] += 6
    connect.poll()
    with pytest.raises(ForgeError, match="not ready"):
        connect.repository()
    with pytest.raises(ForgeError, match="authorized"):
        connect.select(999)
    connect.select(11)
    assert connect.repository() == "synthetic/one"
    assert connect.token() == connect.token() == "synthetic-token-11"
    with pytest.raises(ForgeError, match="disconnect"):
        connect.begin()
    requests = []

    def github_wire(request):
        requests.append(request)
        if request.url.path.endswith("/issues"):
            assert request.headers["authorization"] == "Bearer synthetic-token-11"
            return httpx.Response(
                201, json={"number": 1, "html_url": "https://github.com/synthetic/one/issues/1"}
            )
        return httpx.Response(200, json={"default_branch": "main"})

    forge = ConnectForge(connect, httpx.Client(transport=httpx.MockTransport(github_wire)))
    assert forge.default_branch() == "main"
    assert requests[0].url.path == "/repos/synthetic/one"
    db.save_service(Service(name="api", repo="synthetic/one", ingest_key_hash="12345678"))
    event_log = LogEvent(
        id="synthetic-log-1",
        ts=utcnow(),
        service="api",
        environment="test",
        severity=Severity.CRITICAL,
        message="Synthetic connection test",
        source="webhook",
    )
    result = IngestPipeline(db, forge).handle_event(event_log)
    assert result.outcome == Outcome.TICKETED
    assert requests[-1].url.path == "/repos/synthetic/one/issues"
    db.save_service(Service(name="api", repo="synthetic/other", ingest_key_hash="12345678"))
    fingerprint = db.get_fingerprint(result.fingerprint_hash)
    with pytest.raises(ForgeError, match="connected repository"):
        forge.create_ticket(fingerprint, "wrong repo", "must not be sent")
    event = {"installation": {"id": 91}, "repository": {"id": 11, "full_name": "synthetic/one"}}
    deliver(api, "ping", event)
    connect.receive()
    assert db.claim_delivery("__webhook__", "connect:event-1") is not None
    connect.receive()
    connect.disconnect()
    assert connect.state() == {}
    with pytest.raises(ForgeError, match="not paired"):
        connect.request("GET", "/v1/events")
    db.close()


def test_existing_repo_cannot_be_rebound(harness, tmp_path):
    api, _, _, clock, box = harness
    db = SQLiteStorage(tmp_path / "local.db", secret_box=box)
    db.save_service(Service(name="existing", repo="synthetic/other", ingest_key_hash="12345678"))
    connect = ConnectClient("https://connect.test", db, box, http=api)
    connect.begin()
    clock[0] += 6
    connect.poll()
    with pytest.raises(ForgeError, match="different repo"):
        connect.select(11)
    db.close()


@pytest.mark.parametrize(
    "url", ["http://connect.test", "https://user:pass@connect.test", "https://connect.test?x=1"]
)
def test_connect_endpoint_requires_https_and_no_embedded_credentials(tmp_path, url):
    db = SQLiteStorage(":memory:")
    with pytest.raises(ValueError, match="HTTPS"):
        ConnectClient(url, db, SecretBox("ab" * 32))
    db.close()


def test_bootstrap_key_persists_and_refuses_unsafe_files(tmp_path):
    first = persistent_master_key(tmp_path)
    assert first == persistent_master_key(tmp_path)
    path = tmp_path / "master.key"
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner"):
        persistent_master_key(tmp_path)


def test_composed_connect_is_live_without_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, auto_master_key=True, connect_url="https://connect.test")
    components = compose(settings)
    assert isinstance(components.forge, ConnectForge)
    assert components.runtime.connect_poll is not None
    assert components.secret_box is not None
    components.storage.close()


def test_setup_csrf_and_secret_free_pages(harness, tmp_path):
    api, _, _, clock, box = harness
    db = SQLiteStorage(tmp_path / "local.db", secret_box=box)
    connect = ConnectClient("https://connect.test", db, box, http=api)
    app = create_app(Settings(), db, forge=ConnectForge(connect))
    with TestClient(app, follow_redirects=False) as browser:
        assert browser.get("/connect").status_code == 302
        token, _ = app.state.auth.ensure_setup_token()
        browser.post("/login", data={"token": token})
        assert browser.get("/setup").headers["location"] == "/connect"
        assert browser.post("/connect/begin").status_code == 403
        page = browser.get("/connect")
        csrf = re.search('name="csrf" value="([^"]+)"', page.text)[1]
        data = {"csrf": csrf}
        assert "ABCD-EFGH" in browser.post("/connect/begin", data=data).text
        assert connect.state()["credential"] not in browser.get("/connect").text
        clock[0] += 6
        assert "synthetic/one" in browser.post("/connect/poll", data=data).text
        assert (
            "GitHub connected"
            in browser.post("/connect/select", data={**data, "repository_id": 11}).text
        )
        form = browser.get("/setup").text
        assert 'value="synthetic/one" readonly' in form
        assert 'name="name" value="one"' in form
        assert (
            browser.post(
                "/api/services", json={"name": "bad", "repo": "synthetic/other"}
            ).status_code
            == 400
        )
        assert (
            browser.post(
                "/api/services", json={"name": "good", "repo": "synthetic/one"}
            ).status_code
            == 201
        )
        assert "Unknown" in browser.post("/connect/unknown", data=data).text
        assert "One App" in browser.post("/connect/disconnect", data=data).text
    assert "not configured" in render_connect(None, "csrf")
    db.close()


def test_connect_outage_never_returns_credentials(harness, tmp_path):
    api, _, github, _, box = harness
    headers = paired(harness)

    def broken(*args):
        raise httpx.ConnectError("synthetic sensitive provider detail")

    github.token = broken
    response = api.post("/v1/token", headers=headers)
    assert response.status_code == 503
    assert "sensitive provider detail" not in response.text
    db = SQLiteStorage(":memory:", secret_box=box)
    connect = ConnectClient("https://connect.test", db, box, http=api)
    connect.save({"credential": headers["Authorization"][7:]})
    with pytest.raises(ForgeError, match="unavailable"):
        connect.request("POST", "/v1/token")
    db.close()


def test_admission_limit_before_github_contact(harness):
    _, store, _, clock, _ = harness
    for _ in range(60):
        store.admit_pairing()
    with pytest.raises(ConnectDenied, match="rate limit"):
        store.admit_pairing()
    clock[0] += 61
    store.admit_pairing()


def test_queue_and_pairing_capacity_caps(harness):
    _, store, _, clock, _ = harness
    with store.db:
        store.db.executemany(
            "INSERT INTO connections VALUES (?, ?, 'pending', ?, 0, 'unused')",
            [(str(i), str(i), clock[0] + 1000) for i in range(1000)],
        )
    with pytest.raises(ConnectDenied, match="capacity"):
        store.create({"interval": 5}, 900)
    with store.db:
        store.db.executemany(
            "INSERT INTO relay VALUES ('synthetic', ?, 'unused', ?, 0)",
            [(str(i), clock[0]) for i in range(1000)],
        )
    with pytest.raises(ConnectDenied, match="queue full"):
        store.enqueue("synthetic", "new", {})


def test_master_key_symlink_refused(tmp_path):
    secret = tmp_path / "external"
    secret.write_text("not-a-key")
    (tmp_path / "master.key").symlink_to(secret)
    with pytest.raises(OSError):
        persistent_master_key(tmp_path)


def test_dotenv_inline_comment_regression(monkeypatch):
    monkeypatch.delenv("WAKEY_ALLOWED_REPOS", raising=False)
    monkeypatch.setattr(
        "pathlib.Path.read_text",
        lambda *a, **k: 'WAKEY_ALLOWED_REPOS=synthetic/repo # comment\nBAD="unterminated',
    )
    load_dotenv("synthetic")
    assert os.environ["WAKEY_ALLOWED_REPOS"] == "synthetic/repo"


def test_compose_requires_encryption_for_connect(tmp_path):
    with pytest.raises(ValueError, match="encrypted"):
        compose(Settings(data_dir=tmp_path, connect_url="https://connect.test"))


def test_broker_entrypoint_closes_resources(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["wakey-connect", "--data-dir", str(tmp_path)])
    monkeypatch.setenv("WAKEY_CONNECT_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("WAKEY_CONNECT_APP_SLUG", "synthetic-wakey")
    for name in ("MASTER_KEY", "PRIVATE_KEY", "WEBHOOK_SECRET"):
        monkeypatch.setenv("WAKEY_CONNECT_" + name + "_FILE", "synthetic-secret-file")
    monkeypatch.setattr("pathlib.Path.read_text", lambda *a, **k: "ab" * 32)
    store = Mock()
    github = Mock()
    run = Mock()
    monkeypatch.setattr("wakey.connect.ConnectStore", Mock(return_value=store))
    monkeypatch.setattr("wakey.connect.ConnectGitHub", Mock(return_value=github))
    monkeypatch.setattr("wakey.connect.uvicorn.run", run)
    connect_main()
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8480, "access_log": False}
    store.close.assert_called_once()
    github.http.close.assert_called_once()
    monkeypatch.delenv("WAKEY_CONNECT_MASTER_KEY_FILE")
    with pytest.raises(SystemExit):
        connect_main()


def test_broker_readiness_and_malformed_signed_events(harness):
    api, store, _, _, _ = harness
    assert api.get("/readyz").json() == {"status": "ready"}
    assert deliver(api, "ping", {"repository": None}).status_code == 400
    assert deliver(api, "ping", {"repositories_removed": [None]}).status_code == 400
    store.close()
    assert api.get("/readyz").status_code == 503
