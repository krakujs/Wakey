# SPDX-License-Identifier: Apache-2.0
"""Lifecycle pass + recurrence reopen tests (WF-04, E6-T3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wakey.core.composition import compose
from wakey.core.config import Settings
from wakey.core.models import Fingerprint, LogEvent, Severity, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.fingerprints.engine import fingerprint_from_event
from wakey.forge.port import ConsoleForge
from wakey.ingest.pipeline import IngestPipeline


def make_fp(
    state: WorkState, last_seen: datetime, fp_hash: str = "3f9a1b2c4d5e6f70"
) -> Fingerprint:
    return Fingerprint(
        fp_hash=fp_hash,
        service="payments-api",
        environment="prod",
        template="TypeError: boom",
        severity=Severity.ERROR,
        occurrences=9,
        state=state,
        ticket_issue_id="42",
        ticket_url="https://github.sim/acme/payments/issues/42",
        last_seen=last_seen,
    )


def test_lifecycle_pass_auto_closes_silent_tickets(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    components = compose(settings)
    try:
        silent = datetime.now(UTC) - timedelta(hours=2)
        components.storage.save_fingerprint(make_fp(WorkState.OPEN, silent))
        components.lifecycle()

        stored = components.storage.get_fingerprint("3f9a1b2c4d5e6f70")
        assert stored is not None and stored.state is WorkState.CLOSED_AUTO
        assert len(components.forge.closed) == 1  # type: ignore[attr-defined]
    finally:
        components.storage.close()


def test_lifecycle_pass_leaves_active_tickets_alone(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    components = compose(settings)
    try:
        recent = datetime.now(UTC) - timedelta(minutes=2)
        components.storage.save_fingerprint(make_fp(WorkState.OPEN, recent))
        components.lifecycle()
        stored = components.storage.get_fingerprint("3f9a1b2c4d5e6f70")
        assert stored is not None and stored.state is WorkState.OPEN
    finally:
        components.storage.close()


def test_recurrence_after_auto_close_reopens_not_retickets(tmp_path: Path) -> None:
    """WF-04: recurrence on CLOSED_AUTO reopens the same ticket, never a new one."""
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    event = LogEvent(
        id="evt-recurrence001",
        ts=datetime.now(UTC),
        service="payments-api",
        environment="prod",
        severity=Severity.ERROR,
        message="TypeError: boom",
        source="ingest",
    )
    # the stored fingerprint must carry the hash the event will compute
    stored_hash = fingerprint_from_event(event).fp_hash
    closed = datetime.now(UTC) - timedelta(hours=3)
    storage.save_fingerprint(make_fp(WorkState.CLOSED_AUTO, closed, stored_hash))

    result = IngestPipeline(storage, forge).handle_event(event)
    assert result.outcome.value == "absorbed"
    assert storage.get_fingerprint(stored_hash).state is WorkState.REOPENED
    assert len(forge.reopened) == 1
    storage.close()


def test_dropped_fingerprint_stays_suppressed(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    event = LogEvent(
        id="evt-dropped-suppress",
        ts=datetime.now(UTC),
        service="payments-api",
        environment="prod",
        severity=Severity.ERROR,
        message="TypeError: boom",
        source="ingest",
    )
    stored_hash = fingerprint_from_event(event).fp_hash
    storage.save_fingerprint(make_fp(WorkState.DROPPED, datetime.now(UTC), stored_hash))
    result = IngestPipeline(storage, forge).handle_event(event)
    assert result.outcome.value == "suppressed"
    storage.close()
