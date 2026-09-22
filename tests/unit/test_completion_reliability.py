# SPDX-License-Identifier: Apache-2.0
"""Crash/retry and exact-fingerprint regressions from the completion audit."""

import hashlib
import hmac
from contextlib import suppress
from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wakey.core.composition import compose
from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import (
    Delivery,
    DeployEvent,
    Fingerprint,
    LogEvent,
    Service,
    Severity,
    WorkState,
    utcnow,
)
from wakey.core.server import create_app, register_webhooks
from wakey.core.storage import SQLiteStorage
from wakey.fingerprints.engine import fingerprint_from_event
from wakey.forge.port import ConsoleForge
from wakey.ingest.pipeline import IngestPipeline
from wakey.ingest.worker import DeliveryWorker, DeliveryWorkerPool
from wakey.security.redaction import RedactionEngine


def test_immediate_restart_recovers_ticket_without_new_event(tmp_path):
    class Crash(BaseException):
        pass

    class CrashForge(ConsoleForge):
        def create_ticket(self, *args):
            raise Crash()

    path = tmp_path / "restart.db"
    db = SQLiteStorage(path)
    db.save_service(Service(name="audit", repo="synthetic/repo", ingest_key_hash="abcdefgh"))
    db.save_delivery(Delivery(service="audit", delivery_id="crash", payload="ERROR: boom\n" * 3))
    with suppress(Crash):
        DeliveryWorker(db, IngestPipeline(db, CrashForge()), RedactionEngine()).process_next()
    db.close()
    db = SQLiteStorage(path)
    db.recover_stale_deliveries()
    forge = ConsoleForge()
    worker = DeliveryWorker(db, IngestPipeline(db, forge), RedactionEngine())
    while worker.process_next():
        pass
    assert len(forge.created) == 1
    assert db.list_active_fingerprints()[0].occurrences == 3
    assert db.pending_delivery_count() == 0
    db.close()


def test_unrelated_log_does_not_reopen_fixed_fingerprint(tmp_path):
    c = compose(Settings(data_dir=tmp_path), forge=ConsoleForge())
    now = utcnow()
    c.storage.save_service(Service(name="audit", repo="synthetic/repo", ingest_key_hash="abcdefgh"))
    fp = Fingerprint(
        fp_hash="audit-fixed-error",
        service="audit",
        environment="prod",
        template="TypeError: old bug",
        state=WorkState.VERIFYING,
        verification_started_at=now - timedelta(hours=3),
        fix_commit_sha="a" * 40,
    )
    c.storage.save_fingerprint(fp)
    c.storage.save_deploy(
        DeployEvent(
            service="audit", environment="prod", sha="a" * 40, deployed_at=now - timedelta(hours=2)
        )
    )
    event = LogEvent(
        id="healthy-info-1",
        ts=now - timedelta(minutes=10),
        service="audit",
        environment="prod",
        severity=Severity.INFO,
        message="Health check successful",
        source="synthetic",
    )
    c.storage.claim_event(event, fingerprint_from_event(event))
    result = c.watcher.run_once()
    assert result[0].verdict.value == "success"
    assert not c.storage.get_fingerprint(fp.fp_hash).chronic
    c.storage.close()


def test_failed_github_handler_can_retry():
    class FailsOnce:
        calls = 0

        def handle_comment(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic transient failure")

    db = SQLiteStorage(":memory:")
    commands = FailsOnce()
    app = create_app(
        Settings(webhook_secret="synthetic-secret"), db, forge=ConsoleForge(), commands=commands
    )
    body = (
        b'{"action":"created","issue":{"number":1},"sender":{"login":"reviewer"},'
        b'"comment":{"body":"@wakey fix"}}'
    )
    headers = {
        "X-Hub-Signature-256": "sha256="
        + hmac.new(b"synthetic-secret", body, hashlib.sha256).hexdigest(),
        "X-GitHub-Delivery": "audit-retry",
        "X-GitHub-Event": "issue_comment",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/webhooks/github", content=body, headers=headers).status_code >= 500
        retry = client.post("/webhooks/github", content=body, headers=headers)
        assert retry.status_code == 200
        assert retry.json() == {"handled": False}
    assert commands.calls == 2
    db.close()


def test_webhook_survives_restart_without_sender_retry(tmp_path):
    path = tmp_path / "webhook.db"
    db = SQLiteStorage(path)
    # Persist the exact envelope used by the HTTP route, then simulate a crash after claim.
    payload = (
        '{"event":{"action":"created","issue":{"number":1},'
        '"sender":{"login":"alice"},"comment":{"body":"@wakey status"}},'
        '"kind":"issue_comment"}'
    )
    db.save_delivery(Delivery(service="__webhook__", delivery_id="restart", payload=payload))
    assert db.claim_delivery("__webhook__", "restart") is not None
    db.close()
    db = SQLiteStorage(path)
    assert db.recover_stale_deliveries() == 1
    forge = ConsoleForge()
    worker = DeliveryWorker(db, IngestPipeline(db, forge), RedactionEngine())
    calls = []

    class Commands:
        def handle_comment(self, *args):
            calls.append(args)

    app = FastAPI()
    register_webhooks(
        app,
        settings=Settings(),
        storage=db,
        metrics=MetricsRegistry(),
        forge=forge,
        commands=Commands(),
        runtime=SimpleNamespace(pool=DeliveryWorkerPool(worker)),
    )
    assert worker.process_next()
    assert calls == [("alice", "1", "@wakey status")]
    assert not worker.process_next()
    db.close()


def test_legacy_unbound_events_keep_verification_inconclusive(tmp_path):
    c = compose(Settings(data_dir=tmp_path), forge=ConsoleForge())
    now = utcnow()
    fp = Fingerprint(
        fp_hash="legacy-fixed",
        service="audit",
        environment="prod",
        template="boom",
        state=WorkState.VERIFYING,
        verification_started_at=now - timedelta(hours=3),
    )
    c.storage.save_fingerprint(fp)
    c.storage.save_log_event(
        LogEvent(
            id="old-event-legacy",
            ts=now - timedelta(minutes=5),
            service="audit",
            environment="prod",
            severity=Severity.ERROR,
            message="unclassified legacy row",
            source="test",
        )
    )
    c.watcher.run_once()
    assert c.storage.get_fingerprint(fp.fp_hash).state is WorkState.VERIFYING
    c.storage.close()
