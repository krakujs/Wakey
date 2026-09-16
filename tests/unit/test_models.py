# SPDX-License-Identifier: Apache-2.0
"""Unit tests for core domain models (E2-T1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wakey.core.models import (
    AuditEvent,
    Fingerprint,
    LogEvent,
    Service,
    Severity,
    TraceFrame,
    WorkState,
)


def make_event(**overrides: object) -> LogEvent:
    defaults: dict[str, object] = {
        "id": "evt-12345678",
        "ts": datetime(2026, 9, 16, tzinfo=UTC),
        "service": "payments-api",
        "environment": "prod",
        "severity": Severity.ERROR,
        "message": "Connection refused to payments-db:5432",
        "source": "webhook",
    }
    defaults.update(overrides)
    return LogEvent(**defaults)  # type: ignore[arg-type]


def test_log_event_roundtrip_and_freeze() -> None:
    event = make_event(frames=(TraceFrame(path="app/db.py", line=87, function="connect"),))
    assert event.frames[0].path == "app/db.py"
    with pytest.raises(ValidationError):
        event.message = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["id", "service", "environment", "message", "source"])
def test_log_event_rejects_empty_required_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        make_event(**{field: ""})


def test_severity_rank_orders_correctly() -> None:
    assert Severity.DEBUG.rank < Severity.WARNING.rank < Severity.CRITICAL.rank


def test_fingerprint_defaults_and_busy_states() -> None:
    fp = Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="Connection refused to payments-db:{port}",
    )
    assert fp.state is WorkState.NEW
    assert fp.occurrences == 0
    assert WorkState.INVESTIGATING.busy is True
    assert WorkState.OPEN.busy is False
    assert WorkState.FIXING.busy is True


def test_service_repo_pattern() -> None:
    assert (
        Service(
            name="payments-api",
            repo="acme/payments",
            ingest_key_hash="0" * 16,
        ).repo
        == "acme/payments"
    )
    with pytest.raises(ValidationError):
        Service(name="x", repo="not-a-repo", ingest_key_hash="0" * 16)


def test_audit_event_defaults_timestamp() -> None:
    event = AuditEvent(actor="system", action="fingerprint.create", subject="fp:3f9a")
    assert event.ts.tzinfo is UTC
