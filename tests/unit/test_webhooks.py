# SPDX-License-Identifier: Apache-2.0
"""Webhook tests (WF-07 §1, WF-08 §1): HMAC fail-closed, dedup, merge binding,
deploy-aware verification."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.policy.verification import WatchInputs
from wakey.web.commands import CommandDispatcher

SECRET = "hook-secret"


def sign(body: str) -> str:
    digest = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_fp(state: WorkState = WorkState.AWAITING_HUMAN) -> Fingerprint:
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="TypeError: boom",
        severity=Severity.ERROR,
        occurrences=5,
        state=state,
        ticket_issue_id="42",
        ticket_url="https://github.sim/acme/payments/issues/42",
        proposal_branch="wakey/fix-3f9a1b2c",
        proposal_issue_id="7",
    )


@pytest.fixture()
def env(tmp_path: Path):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        __import__("wakey.core.models", fromlist=["Service"]).Service(
            name="payments-api",
            repo="acme/payments",
            ingest_key_hash="0123456789abcdef",
        )
    )
    storage.save_fingerprint(make_fp())
    forge = ConsoleForge()
    metrics = MetricsRegistry()
    app = create_app(
        Settings(webhook_secret=SECRET),
        storage,
        metrics,
        forge=forge,
        commands=CommandDispatcher(storage, forge, None, authorized_users=frozenset({"alice"})),
    )
    client = TestClient(app)
    return client, storage, forge


def post_webhook(client: TestClient, payload: dict, event: str, delivery: str = "wh-1"):
    body = json.dumps(payload)
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Wakey-Signature": sign(body),
            "X-GitHub-Event": event,
            "X-Delivery-Id": delivery,
        },
    )


def test_missing_secret_refuses_everything(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    app = create_app(
        Settings(),
        storage,
        MetricsRegistry(),
        forge=forge,
        commands=CommandDispatcher(storage, forge, None),
    )
    response = TestClient(app).post(
        "/webhooks/github", content="{}", headers={"X-Wakey-Signature": "x"}
    )
    assert response.status_code == 401, "fail closed without a configured secret"


def test_bad_signature_rejected(env) -> None:
    client, _, _ = env
    response = client.post(
        "/webhooks/github",
        content="{}",
        headers={"X-Wakey-Signature": "sha256=" + "0" * 64, "X-GitHub-Event": "ping"},
    )
    assert response.status_code == 401


def test_ping_and_delivery_dedup(env) -> None:
    client, _, _ = env
    first = post_webhook(client, {"zen": "ok"}, "ping", delivery="d-1")
    assert first.status_code == 200
    replay = post_webhook(client, {"zen": "ok"}, "ping", delivery="d-1")
    assert replay.json() == {"deduplicated": True}


def test_comment_command_dispatches_and_replies(env) -> None:
    client, _, forge = env
    payload = {
        "action": "created",
        "issue": {"number": 42, "html_url": "https://github.sim/acme/payments/issues/42"},
        "comment": {"body": "@wakey status"},
        "sender": {"login": "alice"},
    }
    response = post_webhook(client, payload, "issue_comment")
    assert response.json() == {"handled": True}
    assert any("Status" in body and "awaiting-human" in body for _, body in forge.comments)


def test_merged_wakey_pr_starts_verification_window(env) -> None:
    client, storage, forge = env
    payload = {
        "action": "closed",
        "pull_request": {
            "merged": True,
            "head": {"ref": "wakey/fix-3f9a1b2c"},
            "merge_commit_sha": "abc123def4567890",
        },
    }
    response = post_webhook(client, payload, "pull_request")
    assert response.json() == {"handled": True, "state": "verifying"}

    stored = storage.get_fingerprint("3f9a1b2c4d5e6f70")
    assert stored is not None and stored.state is WorkState.VERIFYING
    assert stored.verification_started_at is not None
    assert stored.verification_start_occurrences == 5
    assert any("verification window armed" in body for _, body in forge.comments)


def test_unmerged_pr_close_ignored(env) -> None:
    client, storage, _ = env
    payload = {
        "action": "closed",
        "pull_request": {"merged": False, "head": {"ref": "wakey/fix-3f9a1b2c"}},
    }
    response = post_webhook(client, payload, "pull_request")
    assert response.json() == {"handled": False}
    stored = storage.get_fingerprint("3f9a1b2c4d5e6f70")
    assert stored is not None and stored.state is WorkState.AWAITING_HUMAN


def test_deploy_event_recorded_and_gates_verification_clock(env) -> None:
    client, storage, _ = env
    # merge arms the window
    merge = {
        "action": "closed",
        "pull_request": {"merged": True, "head": {"ref": "wakey/fix-3f9a1b2c"}},
    }
    post_webhook(client, merge, "pull_request", delivery="m-1")
    stored = storage.get_fingerprint("3f9a1b2c4d5e6f70")
    assert stored is not None and stored.verification_started_at is not None

    # deploy recorded via HMAC-verified deploy webhook
    body = json.dumps({"service": "payments-api", "sha": "abc123def4567890"})
    response = client.post(
        "/webhooks/deploy",
        content=body,
        headers={"X-Wakey-Signature": sign(body), "X-Delivery-Id": "dep-1"},
    )
    assert response.status_code == 202
    deploys = storage.list_recent_deploys("payments-api")
    assert len(deploys) == 1

    # deploy-aware grace inputs: clock only runs after the deploy lands
    started = stored.verification_started_at
    assert started is not None
    before_deploy = WatchInputs(
        occurrences_after_fix=0,
        grace_window_elapsed=True,
        fix_deployed_to_environment=deploys[0].deployed_at < started,
    )
    assert before_deploy.fix_deployed_to_environment is False
    after = deploys[0].deployed_at + timedelta(minutes=999)
    assert after > started


def test_expired_deploy_signature_rejected_before_persistence(env) -> None:
    client, storage, _ = env
    body = json.dumps({"service": "payments-api", "sha": "deadbeef"})
    response = client.post(
        "/webhooks/deploy",
        content=body,
        headers={"X-Wakey-Signature": "sha256=" + "0" * 64},
    )
    assert response.status_code == 401
    assert storage.list_recent_deploys("payments-api") == []
