# SPDX-License-Identifier: Apache-2.0
"""Postgres backend integration tests (OPS-5).

Run with a live Postgres:

    docker run --rm -p 55432:5432 -e POSTGRES_PASSWORD=wakey \
      -e POSTGRES_DB=wakey postgres:16
    WAKEYPG_TEST_DSN=postgresql://postgres:wakey@127.0.0.1:55432/wakey pytest

Tests are skipped when the server is not reachable.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from wakey.core.models import (
    AuditEvent,
    Delivery,
    DeployEvent,
    Fingerprint,
    LogEvent,
    Service,
    Severity,
    WorkState,
)
from wakey.storage.postgres import PostgresStorage, _fingerprint_params

DSN = os.environ.get("WAKEYPG_TEST_DSN", "postgresql://postgres:wakey@127.0.0.1:55432/wakey")


def _pg_up() -> bool:
    try:
        conn = psycopg.connect(DSN, connect_timeout=3)
    except Exception:  # pragma: no cover — environment without Postgres
        return False
    conn.close()
    return True


pytestmark = pytest.mark.skipif(not _pg_up(), reason="Postgres not reachable")


@pytest.fixture()
def storage(request):
    """One fresh database per xdist worker — parallel-safe isolation (OPS-5)."""
    worker = getattr(request.config, "workerinput", {}).get("workerid", "master")
    dbname = f"wakey_test_{worker}"
    base = DSN.rsplit("/", 1)[0]
    admin = psycopg.connect(base + "/postgres", autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    admin.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()
    st = PostgresStorage(f"{base}/{dbname}")
    yield st
    st.close()


def make_fp(state: WorkState = WorkState.AWAITING_HUMAN) -> Fingerprint:
    return Fingerprint(
        fp_hash="pgtest00000000001",
        service="pg-svc",
        environment="prod",
        template="TypeError: boom",
        severity=Severity.ERROR,
        occurrences=2,
        state=state,
        ticket_issue_id="7",
        ticket_url="https://github.example/acme/repo/issues/7",
    )


def test_round_trip_and_duplicate_rejection(storage) -> None:
    fp = make_fp()
    storage.save_fingerprint(fp)
    stored = storage.get_fingerprint(fp.fp_hash)
    assert stored == fp, "full round trip must preserve every field"

    event = LogEvent(
        id="pg-evt-00000000001",
        ts=datetime.now(UTC),
        service="pg-svc",
        environment="prod",
        severity=Severity.ERROR,
        message="boom",
        source="ingest",
    )
    assert storage.save_log_event(event) is True
    assert storage.save_log_event(event) is False, "duplicate event id rejected"
    assert not storage.is_event_dispatched(event.id)
    storage.mark_event_dispatched(event.id)
    assert storage.is_event_dispatched(event.id)


def test_delivery_lifecycle(storage) -> None:
    delivery = Delivery(service="pg-svc", delivery_id="d-1", payload="raw")
    assert storage.save_delivery(delivery) is True
    assert storage.save_delivery(delivery) is False, "replay deduplicated"

    claimed = storage.claim_next_delivery()
    assert claimed is not None and claimed.delivery_id == "d-1"
    assert storage.claim_next_delivery() is None, "single pending delivery"

    storage.fail_delivery("pg-svc", "d-1", max_attempts=2)
    assert storage.claim_next_delivery() is not None, "requeued after failure 1"
    storage.fail_delivery("pg-svc", "d-1", max_attempts=2)
    assert storage.pending_delivery_count() == 0, "dead-lettered at cap"


def test_recover_stale_processing(storage) -> None:
    delivery = Delivery(service="pg-svc", delivery_id="d-x", payload="raw")
    storage.save_delivery(delivery)
    storage.claim_next_delivery()  # crash sim: claimed but never completed
    assert storage.recover_stale_deliveries() == 1
    assert storage.pending_delivery_count() == 1


def test_secrets_round_trip(storage) -> None:
    storage.set_secret("k", "v1")
    storage.set_secret("k", "v2")
    assert storage.get_secret("k") == "v2"
    storage.delete_secret("k")
    assert storage.get_secret("k") is None


def test_deploys_recorded(storage) -> None:
    storage.save_deploy(DeployEvent(service="pg-svc", environment="prod", sha="abc123def456"))
    deploys = storage.list_recent_deploys("pg-svc")
    assert len(deploys) == 1 and deploys[0].sha == "abc123def456"


def test_audit_chain_verification(storage) -> None:
    storage.record_audit(AuditEvent(actor="a", action="one", subject="s"))
    storage.record_audit(AuditEvent(actor="b", action="two", subject="s"))
    ok, count = storage.verify_audit_chain()
    assert ok and count >= 2


def test_audit_list_newest_first(storage) -> None:
    storage.record_audit(AuditEvent(actor="x", action="first", subject="fp:z"))
    storage.record_audit(AuditEvent(actor="x", action="second", subject="fp:z"))
    rows = storage.list_audit(subject="fp:z", limit=5)
    assert rows[0].action == "second", "newest first"


def test_services_crud_and_lookup(storage) -> None:
    service = Service(
        name="pg-svc",
        repo="acme/pg",
        ingest_key_hash="0123456789abcdef",
        webhook_secret="plain-hook-secret",
    )
    storage.save_service(service)
    assert storage.has_services() is True
    assert [s.name for s in storage.list_services()] == ["pg-svc"]
    got = storage.get_service_by_ingest_key_hash("0123456789abcdef")
    assert got is not None and got.name == "pg-svc"
    assert got.webhook_secret == "plain-hook-secret"


def test_deploys_and_events_prune(storage) -> None:
    storage.save_deploy(DeployEvent(service="pg-svc", environment="prod", sha="abc123def456"))
    storage.prune_events(datetime.now(UTC) + timedelta(days=1))
    assert storage.list_recent_deploys("pg-svc"), "deploys kept until cutoff"


def test_fingerprint_issue_and_branch_lookup(storage) -> None:
    fp = make_fp().model_copy(update={"proposal_branch": "wakey/fix-pgtest"})
    storage.save_fingerprint(fp)
    assert storage.get_fingerprint_by_issue("7").fp_hash == fp.fp_hash
    assert storage.get_fingerprint_by_branch("wakey/fix-pgtest").fp_hash == fp.fp_hash
    assert storage.get_fingerprint_by_branch("wakey/nope") is None


def test_occurrence_upsert_preserves_db_truth(storage) -> None:
    anchored = make_fp(WorkState.QUEUED_RCA).model_copy(
        update={"occurrences": 4, "chronic": True, "commented_at_occurrences": 4}
    )
    storage.save_fingerprint(anchored)
    fresh = make_fp(WorkState.NEW).model_copy(
        update={"last_seen": datetime.now(UTC) + timedelta(minutes=5), "occurrences": 0}
    )
    merged = storage.record_occurrence(fresh, delta=1)
    assert merged.occurrences == 5, "stored 4 + incoming 0 + delta 1"
    assert merged.state is WorkState.QUEUED_RCA, "state is DB truth"
    assert merged.chronic is True


def test_backup_not_supported_points_to_pg_dump(storage) -> None:
    with pytest.raises(NotImplementedError, match="pg_dump"):
        storage.backup(Path("/tmp/unused-backup"))


def test_secrets_crud(storage) -> None:
    storage.set_secret("setup_token", "raw-secret-value")
    assert storage.get_secret("setup_token") == "raw-secret-value"
    storage.set_secret("setup_token", "replaced")
    assert storage.get_secret("setup_token") == "replaced"
    storage.delete_secret("setup_token")
    assert storage.get_secret("setup_token") is None
    assert storage.get_secret("never-existed") is None


def test_audit_record_verify_and_list(storage) -> None:
    storage.record_audit(AuditEvent(actor="a", action="first", subject="fp:x"))
    storage.record_audit(AuditEvent(actor="b", action="second", subject="fp:x"))
    ok, count = storage.verify_audit_chain()
    assert ok and count == 2
    rows = storage.list_audit(subject="fp:x", limit=10)
    assert len(rows) == 2 and rows[0].action == "second"


def test_deploys_round_trip(storage) -> None:

    storage.save_deploy(
        DeployEvent(
            service="pg-svc", environment="prod", sha="abc123", deployed_at=datetime.now(UTC)
        )
    )
    deploys = storage.list_recent_deploys("pg-svc")
    assert len(deploys) == 1 and deploys[0].sha == "abc123"


def test_recent_events_returns_stored_events(storage) -> None:
    for i in range(3):
        storage.save_log_event(
            LogEvent(
                id=f"pg-evt-recent-{i}",
                ts=datetime.now(UTC),
                service="recent-svc",
                environment="prod",
                severity=Severity.ERROR,
                message=f"error {i}",
                source="ingest",
            )
        )
    events = storage.recent_events("recent-svc", limit=2)
    assert len(events) == 2
    assert all("error" in e.message for e in events)


def test_additional_coverage_paths(storage) -> None:
    # backup is SQLite-only on the ABC
    with pytest.raises(NotImplementedError, match="pg_dump"):
        storage.backup(Path("/tmp/unused"))
    # fail_delivery on a missing delivery raises
    with pytest.raises(KeyError):
        storage.fail_delivery("nope", "nope", max_attempts=1)
    # meta table records the schema version
    row = storage._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()  # noqa: SLF001
    assert row is not None and int(row["value"]) >= 1


def test_fingerprint_params_round_trip(storage) -> None:
    fp = make_fp()
    params = _fingerprint_params(fp)
    assert len(params) == 20 and params[0] == fp.fp_hash
