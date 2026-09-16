# SPDX-License-Identifier: Apache-2.0
"""Unit tests for SQLiteStorage (E2-T3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from wakey.core.models import (
    AuditEvent,
    Fingerprint,
    LogEvent,
    Service,
    Severity,
    TraceFrame,
    WorkState,
)
from wakey.core.storage import SQLiteStorage


@pytest.fixture()
def storage(tmp_path: pytest.TempPathFactory) -> SQLiteStorage:  # type: ignore[name-defined]
    return SQLiteStorage(tmp_path / "wakey.db")  # type: ignore[return-value]


def make_fingerprint(**overrides: object) -> Fingerprint:
    defaults: dict[str, object] = {
        "fp_hash": "3f9a1b2c4d5e6f70",
        "service": "payments-api",
        "environment": "prod",
        "template": "Connection refused to payments-db:{port}",
        "frames": (TraceFrame(path="app/db.py", line=87, function="connect"),),
        "severity": Severity.ERROR,
        "last_seen": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Fingerprint(**defaults)  # type: ignore[arg-type]


def test_ready_and_idempotent_migration(tmp_path: object) -> None:
    s1 = SQLiteStorage(tmp_path / "wakey.db")  # type: ignore[attr-defined]
    assert s1.check_ready()
    s1.close()
    s2 = SQLiteStorage(tmp_path / "wakey.db")  # type: ignore[attr-defined]  # reopen
    assert s2.check_ready()
    s2.close()


def test_delivery_dedup(storage: SQLiteStorage) -> None:
    assert storage.record_delivery("d-1") is True
    assert storage.record_delivery("d-1") is False
    assert storage.record_delivery("d-2") is True


def test_occurrence_accumulation_preserves_first_seen(storage: SQLiteStorage) -> None:
    first = make_fingerprint(last_seen=datetime.now(UTC))
    stored = storage.record_occurrence(first)
    assert stored.occurrences == 1

    later = make_fingerprint(
        last_seen=datetime.now(UTC) + timedelta(minutes=5),
        state=WorkState.INVESTIGATING,
    )
    merged = storage.record_occurrence(later, delta=9)
    assert merged.occurrences == 10
    assert merged.state is WorkState.INVESTIGATING  # caller owns state
    assert merged.first_seen == stored.first_seen  # DB preserves origin

    fetched = storage.get_fingerprint(first.fp_hash)
    assert fetched is not None
    assert fetched.occurrences == 10
    assert fetched.frames[0].function == "connect"
    assert fetched.severity is Severity.ERROR


def test_service_roundtrip(storage: SQLiteStorage) -> None:
    service = Service(name="payments-api", repo="acme/payments", ingest_key_hash="a" * 16)
    storage.save_service(service)
    fetched = storage.get_service("payments-api")
    assert fetched == service
    assert storage.get_service("nope") is None

    renamed = service.model_copy(update={"repo": "acme/payments-v2"})
    storage.save_service(renamed)
    assert storage.get_service("payments-api") == renamed


def test_log_event_and_audit(storage: SQLiteStorage) -> None:
    event = LogEvent(
        id="evt-abcdef12",
        ts=datetime.now(UTC),
        service="payments-api",
        environment="prod",
        severity=Severity.CRITICAL,
        message="OOM killed",
        source="gcp-pubsub",
        attributes={"zone": "eu-west1"},
    )
    storage.save_log_event(event)
    storage.record_audit(
        AuditEvent(
            actor="system", action="ticket.create", subject="fp:3f9a", details={"sev": "high"}
        )
    )
    assert storage.check_ready()
