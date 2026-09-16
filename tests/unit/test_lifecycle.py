# SPDX-License-Identifier: Apache-2.0
"""Lifecycle decision tests (E6-T2/T3/T4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.tickets.lifecycle import decide


def make_fp(state: WorkState, last_seen: datetime, with_ticket: bool = True) -> Fingerprint:
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="boom",
        severity=Severity.ERROR,
        state=state,
        last_seen=last_seen,
        ticket_issue_id="1" if with_ticket else None,
        ticket_url="console://tickets/1" if with_ticket else None,
    )


def test_active_ticket_within_grace_is_left_alone() -> None:
    now = datetime.now(UTC)
    fp = make_fp(WorkState.OPEN, now - timedelta(minutes=5))
    decision = decide(fp, now, grace_minutes=30)
    assert decision.action == "none"


def test_silent_ticket_closes_after_grace() -> None:
    now = datetime.now(UTC)
    fp = make_fp(WorkState.OPEN, now - timedelta(minutes=45))
    decision = decide(fp, now, grace_minutes=30)
    assert decision.action == "close"


def test_recurrence_reopens_closed_ticket() -> None:
    now = datetime.now(UTC)
    fp = make_fp(WorkState.CLOSED_AUTO, now)
    decision = decide(fp, now)
    assert decision.action == "reopen"


def test_ticketless_fingerprint_is_ignored() -> None:
    now = datetime.now(UTC)
    fp = make_fp(WorkState.OPEN, now, with_ticket=False)
    assert decide(fp, now).action == "none"
