# SPDX-License-Identifier: Apache-2.0
"""Regressions for the v0.1.0 review defect reproductions.

Each test pins behavior that reproduced as a defect against the released
alpha and must stay fixed:

1. native GitHub webhook signatures (``X-Hub-Signature-256`` /
   ``X-GitHub-Delivery``, App webhook secret) verify instead of 401;
2. a delivery retried after a mid-batch forge failure counts each event
   exactly once (3 events → 3 occurrences, 1 ticket);
3. a crash-interrupted ticket creation self-heals on the next event
   instead of leaving the fingerprint ticketless forever;
4. verification binds environment, fix commit, and a deploy-relative
   window — a staging deploy never marks a prod fingerprint verified;
5. workspace snapshots never read through symlinks.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from wakey.agents.fix import snapshot_files
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
)
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.fingerprints.engine import fingerprint_from_event
from wakey.forge.github import ForgeError, ForgeUnreachable
from wakey.forge.port import ConsoleForge, TicketRef
from wakey.ingest.pipeline import IngestPipeline
from wakey.ingest.worker import DeliveryWorker
from wakey.security.redaction import RedactionEngine
from wakey.security.secretbox import SecretBox
from wakey.web.commands import CommandDispatcher
from wakey.web.github_setup import SECRET_WEBHOOK_SLUG

KEY = "wk_regression_key"
FIX_SHA = "fix0000000000000000000000000000000000000"  # 40 hex chars
MSG = "ERROR: TypeError boom in handler"


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _error_event(event_id: str, *, ts: datetime | None = None) -> LogEvent:
    return LogEvent(
        id=event_id,
        ts=ts or datetime.now(UTC),
        service="payments-api",
        environment="prod",
        severity=Severity.ERROR,
        message=MSG,
        source="ingest",
    )


def _drain(worker: DeliveryWorker) -> None:
    while worker.process_next():
        pass


def _webhook_client(tmp_path: Path, storage: SQLiteStorage, secret_box: SecretBox | None):  # type: ignore[no-untyped-def]
    """App with the webhook surface registered and no ingest key involved."""
    settings = Settings(data_dir=tmp_path, webhook_secret="hook-secret")
    commands = CommandDispatcher(storage, ConsoleForge(), None, metrics=MetricsRegistry())
    app = create_app(
        settings,
        storage,
        MetricsRegistry(),
        forge=ConsoleForge(),
        commands=commands,
        secret_box=secret_box,
    )
    return TestClient(app)


def _signed(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- 1. native GitHub webhooks -------------------------------------------------

GITHUB_DELIVERY = "72d3162e-cc78-11e6-81ab-4c9367dc09c8"


def test_native_github_signature_and_delivery_headers(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    client = _webhook_client(tmp_path, storage, None)
    body = b'{"zen": "design for failure"}'
    headers = {
        "X-Hub-Signature-256": _signed("hook-secret", body),
        "X-GitHub-Delivery": GITHUB_DELIVERY,
        "X-GitHub-Event": "ping",
        "Content-Type": "application/json",
    }
    response = client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200, "a normally signed GitHub request must verify"

    # GitHub retries carry the same X-GitHub-Delivery — deduplicated
    replay = client.post("/webhooks/github", content=body, headers=headers)
    assert replay.json() == {"deduplicated": True}


def test_native_signature_without_a_match_is_rejected(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    client = _webhook_client(tmp_path, storage, None)
    body = b'{"zen": "keep signing"}'
    headers = {
        "X-Hub-Signature-256": _signed("wrong-secret", body),
        "X-GitHub-Event": "ping",
    }
    assert client.post("/webhooks/github", content=body, headers=headers).status_code == 401


def test_github_app_webhook_secret_verifies_native_signature(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    master = SecretBox("ab" * 32)
    storage.set_secret(SECRET_WEBHOOK_SLUG, master.encrypt("app-hook-secret"))
    settings = Settings(data_dir=tmp_path)  # no env webhook secret at all
    commands = CommandDispatcher(storage, ConsoleForge(), None, metrics=MetricsRegistry())
    app = create_app(
        settings,
        storage,
        MetricsRegistry(),
        forge=ConsoleForge(),
        commands=commands,
        secret_box=master,
    )
    client = TestClient(app)
    body = b'{"action": "created"}'
    headers = {
        "X-Hub-Signature-256": _signed("app-hook-secret", body),
        "X-GitHub-Event": "issue_comment",
    }
    response = client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200, "the App's stored webhook secret must verify"


# --- 2. retry counts exactly once ------------------------------------------------


class FlakyOnceForge(ConsoleForge):
    """ConsoleForge whose first create_ticket raises (simulated outage)."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_times = 1

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("forge outage")
        return super().create_ticket(fingerprint, title, body)


def test_retry_after_forge_failure_never_overcounts(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        Service(name="payments-api", repo="acme/payments", ingest_key_hash=_key_hash(KEY))
    )
    forge = FlakyOnceForge()
    worker = DeliveryWorker(storage, IngestPipeline(storage, forge), RedactionEngine())
    settings = Settings(data_dir=tmp_path, webhook_secret="")
    app = create_app(settings, storage, MetricsRegistry(), forge=forge)
    client = TestClient(app)

    burst = "\n".join(["ERROR: connection refused to db"] * 3)
    response = client.post(f"/ingest/{KEY}", content=burst, headers={"X-Delivery-Id": "d-retry"})
    assert response.status_code == 202

    worker.process_next()  # attempt 1: forge fails on the threshold event
    _drain(worker)  # attempt 2: completes

    assert len(forge.created) == 1, "retry completes ticketing exactly once"
    probe = LogEvent(
        id="evt-probe000001",
        ts=datetime.now(UTC),
        service="payments-api",
        environment="prod",
        severity=Severity.ERROR,
        message="ERROR: connection refused to db",
        source="ingest",
    )
    stored = storage.get_fingerprint(fingerprint_from_event(probe).fp_hash)
    assert stored is not None
    assert stored.occurrences == 3, "3 events must count 3 occurrences, never 4"


# --- 3. crash-interrupted ticket creation self-heals ------------------------------


def test_stale_reservation_without_ticket_is_recovered(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    pipeline = IngestPipeline(storage, forge)

    event = _error_event("evt-recover001")
    fp_hash = fingerprint_from_event(event).fp_hash
    storage.save_fingerprint(
        Fingerprint(
            fp_hash=fp_hash,
            service="payments-api",
            environment="prod",
            template=MSG,
            severity=Severity.ERROR,
            occurrences=2,
            state=WorkState.QUEUED_RCA,
            reserved_at=datetime.now(UTC) - timedelta(seconds=3600),  # dead worker
        )
    )

    result = pipeline.handle_event(event)
    assert result.outcome.value == "ticketed", "stale reservation must re-arm ticketing"
    stored = storage.get_fingerprint(fp_hash)
    assert stored is not None and stored.ticket_url is not None
    assert stored.reserved_at is None


def test_fresh_reservation_stays_absorbed(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    pipeline = IngestPipeline(storage, forge)

    event = _error_event("evt-freshres01")
    fp_hash = fingerprint_from_event(event).fp_hash
    storage.save_fingerprint(
        Fingerprint(
            fp_hash=fp_hash,
            service="payments-api",
            environment="prod",
            template=MSG,
            severity=Severity.ERROR,
            occurrences=9,
            state=WorkState.QUEUED_RCA,
            reserved_at=datetime.now(UTC) - timedelta(seconds=5),  # in flight
        )
    )

    result = pipeline.handle_event(event)
    assert result.outcome.value == "absorbed", "in-flight reservation must not double-ticket"
    assert forge.created == [], "an in-flight reservation never spawns a second ticket"


# --- 4. verification binding (real composition path) ------------------------------


def _verifying_components(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Compose the real system and arm one prod fingerprint in ``verifying``."""
    components = compose(Settings(data_dir=tmp_path))
    storage = components.storage
    event = _error_event("evt-verify00001")
    fp_hash = fingerprint_from_event(event).fp_hash
    storage.save_fingerprint(
        Fingerprint(
            fp_hash=fp_hash,
            service="payments-api",
            environment="prod",
            template=MSG,
            severity=Severity.ERROR,
            occurrences=10,
            state=WorkState.VERIFYING,
            verification_started_at=datetime.now(UTC) - timedelta(hours=3),
            verification_start_occurrences=10,
            fix_commit_sha=FIX_SHA,
        )
    )
    return components, fp_hash


def _deploy(storage: SQLiteStorage, environment: str, sha: str) -> None:
    storage.save_deploy(
        DeployEvent(
            service="payments-api",
            environment=environment,
            sha=sha,
            deployed_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )


def test_staging_deploy_never_marks_prod_verified(tmp_path: Path) -> None:
    components, fp_hash = _verifying_components(tmp_path)
    try:
        _deploy(components.storage, "staging", FIX_SHA)
        components.watcher.run_once()
        stored = components.storage.get_fingerprint(fp_hash)
        assert stored is not None
        assert stored.state is WorkState.VERIFYING, "staging deploy must not verify prod"
    finally:
        components.storage.close()


def test_matching_prod_deploy_closes_the_loop(tmp_path: Path) -> None:
    components, fp_hash = _verifying_components(tmp_path)
    try:
        _deploy(components.storage, "prod", FIX_SHA)
        components.watcher.run_once()
        stored = components.storage.get_fingerprint(fp_hash)
        assert stored is not None
        assert stored.state is WorkState.VERIFIED_CLOSED, "silence after the fix deploy = success"
    finally:
        components.storage.close()


def test_wrong_sha_prod_deploy_stays_inconclusive(tmp_path: Path) -> None:
    components, fp_hash = _verifying_components(tmp_path)
    try:
        _deploy(components.storage, "prod", "0" * 40)  # a deploy without the fix
        components.watcher.run_once()
        stored = components.storage.get_fingerprint(fp_hash)
        assert stored is not None
        assert stored.state is WorkState.VERIFYING, "a deploy that lacks the fix is inconclusive"
    finally:
        components.storage.close()


def test_recurrence_after_fix_deploy_reopens(tmp_path: Path) -> None:
    """Occurrences counted from the fix deploy — not the merge — drive the verdict."""
    components, fp_hash = _verifying_components(tmp_path)
    storage = components.storage
    try:
        _deploy(storage, "prod", FIX_SHA)
        # recurrence two hours AFTER the fix deployed (but before merge+3h window)
        recurrence = _error_event("evt-recurre001", ts=datetime.now(UTC) - timedelta(hours=1))
        storage.claim_event(recurrence, fingerprint_from_event(recurrence))
        components.watcher.run_once()
        stored = storage.get_fingerprint(fp_hash)
        assert stored is not None
        assert stored.state is WorkState.REOPENED, "recurrence after the deploy = insufficient"
        assert stored.chronic, "insufficient verification marks the fingerprint chronic"
    finally:
        components.storage.close()


# --- 5. snapshot symlink containment -------------------------------------------------


def test_snapshot_never_reads_through_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.py"
    secret.write_text("TOKEN = 'leaked'\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (workspace / "evil.py").symlink_to(secret)
    linked_dir = outside / "vendor"
    linked_dir.mkdir()
    (linked_dir / "hidden.py").write_text("HIDDEN = True\n", encoding="utf-8")
    (workspace / "vendor").symlink_to(linked_dir, target_is_directory=True)

    snapshot = snapshot_files(workspace)
    assert "src/app.py" in snapshot
    assert "evil.py" not in snapshot, "file symlinks must never be read through"
    assert not any("hidden" in key for key in snapshot), "symlinked dirs must not be traversed"
    assert all("leaked" not in content for content in snapshot.values())


# --- 6. ambiguous external writes reconcile (R-15) -----------------------------------


class AcceptedThenLostForge(ConsoleForge):
    """Forge whose first create_ticket is accepted remotely, then times out.

    Mirrors the readiness-check reproduction: GitHub created the issue but
    the response never arrived, so the caller cannot know from the
    exception alone whether the write landed.
    """

    def __init__(self) -> None:
        super().__init__()
        self.remote: dict[str, TicketRef] = {}
        self.create_calls = 0
        self.lookup_calls = 0

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
        self.create_calls += 1
        if self.create_calls == 1:
            ref = TicketRef(issue_id="remote-1", url="https://github.com/x/y/issues/1")
            self.remote[fingerprint.fp_hash] = ref
            raise ForgeUnreachable(
                "github POST /issues unreachable (write may have landed): read timeout"
            )
        return super().create_ticket(fingerprint, title, body)

    def find_ticket_by_marker(self, marker: str) -> TicketRef | None:
        self.lookup_calls += 1
        return self.remote.get(marker.removeprefix("fp:"))


def _ticketing_worker(tmp_path: Path, forge: object) -> tuple[SQLiteStorage, object]:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        Service(name="payments-api", repo="acme/payments", ingest_key_hash=_key_hash(KEY))
    )
    worker = DeliveryWorker(storage, IngestPipeline(storage, forge), RedactionEngine())  # type: ignore[arg-type]
    return storage, worker


def test_lost_response_after_accepted_issue_binds_never_duplicates(tmp_path: Path) -> None:
    forge = AcceptedThenLostForge()
    storage, worker = _ticketing_worker(tmp_path, forge)
    storage.save_delivery(
        Delivery(service="payments-api", delivery_id="d-lost", payload=f"{MSG}\n" * 3)
    )

    worker.process_next()  # forge accepted, response lost → reconcile must bind

    stored_forges = list(forge.remote.values())
    assert len(stored_forges) == 1, "one remote issue per intended operation"
    assert forge.create_calls == 1, "a reconciled write must never re-create"
    fp_hash = fingerprint_from_event(_error_event("evt-lost000001")).fp_hash
    stored = storage.get_fingerprint(fp_hash)
    assert stored is not None
    assert stored.ticket_issue_id == "remote-1", "the accepted issue is bound locally"
    rows = storage._conn.execute(  # noqa: SLF001
        "SELECT details_json FROM audit WHERE action='ticket.create'"
    ).fetchall()
    assert rows and '"reconciled": "true"' in rows[-1]["details_json"]
    storage.close()


def test_competing_worker_wins_race_and_retry_binds(tmp_path: Path) -> None:
    """Create fails ambiguously; another worker's ticket exists — bind it."""
    forge = AcceptedThenLostForge()
    storage, worker = _ticketing_worker(tmp_path, forge)

    events = [_error_event(f"evt-race0000{i}") for i in range(1, 4)]
    for event in events:
        storage.claim_event(event, fingerprint_from_event(event))
    pipeline = IngestPipeline(storage, forge)
    result = pipeline.handle_event(events[-1])  # create raises; reconcile finds the twin
    storage.save_delivery(
        Delivery(service="payments-api", delivery_id="d-race", payload=f"{MSG}\n" * 3)
    )

    assert result.outcome.value == "ticketed"
    stored = storage.get_fingerprint(fingerprint_from_event(event).fp_hash)
    assert stored is not None and stored.ticket_issue_id == "remote-1"
    _drain(worker)
    assert forge.create_calls == 1, "retry after reconciliation never re-creates"
    storage.close()


def test_definitive_rejection_releases_and_retries_normally(tmp_path: Path) -> None:
    """4xx-class rejections wrote nothing: reservation released, retry tickets."""
    calls = {"n": 0}

    class RejectedForge(ConsoleForge):
        def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ForgeError("github POST /issues -> 422", status_code=422)
            return super().create_ticket(fingerprint, title, body)

    forge = RejectedForge()
    storage, worker = _ticketing_worker(tmp_path, forge)
    storage.save_delivery(
        Delivery(service="payments-api", delivery_id="d-422", payload=f"{MSG}\n" * 3)
    )

    worker.process_next()  # definitive 422: released for retry
    _drain(worker)

    assert calls["n"] == 2
    assert len(forge.created) == 1, "retry after a definitive rejection tickets exactly once"
    storage.close()
