# SPDX-License-Identifier: Apache-2.0
"""FixProposalDelivery tests (WF-06 §9-10): draft delivery, refusal, audit line."""

from __future__ import annotations

import sqlite3

from wakey.agents.fix import FixResult
from wakey.agents.fix_delivery import FixProposalDelivery, format_proposal_body
from wakey.core.models import Fingerprint, Severity
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge

DIFF = (
    "--- a/billing.py\n"
    "+++ b/billing.py\n"
    "@@ -1 +1 @@\n"
    '-    return order["idempotency_key"]\n'
    '+    return order.get("idempotency_key")\n'
)
RCA_SUMMARY = "KeyError for legacy orders missing the idempotency key"


def make_fingerprint() -> Fingerprint:
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="KeyError: 'idempotency_key'",
        severity=Severity.ERROR,
        occurrences=5,
    )


def audit_rows(storage: SQLiteStorage) -> list[sqlite3.Row]:
    """The audit table has no read API; in-memory storage can only be read in place."""
    rows: list[sqlite3.Row] = storage._conn.execute(  # noqa: SLF001
        "SELECT actor, action, subject FROM audit ORDER BY seq"
    ).fetchall()
    return rows


def test_successful_delivery_opens_draft_and_audits() -> None:
    storage = SQLiteStorage(":memory:")
    forge = ConsoleForge()
    fingerprint = make_fingerprint()

    result = FixProposalDelivery(storage, forge).deliver(
        fingerprint, FixResult(True, "repro green after 1 patch attempt(s)", DIFF, 1), RCA_SUMMARY
    )

    assert result == {
        "branch": "wakey/fix-3f9a1b2c",
        "url": "console://pulls/wakey/fix-3f9a1b2c",
        "delivered": True,
    }
    assert len(forge.proposals) == 1
    proposal = forge.proposals[0]
    assert proposal["branch"] == "wakey/fix-3f9a1b2c"
    assert proposal["title"] == "[wakey] Fix for fp:3f9a1b2c4d5e6f70 (draft)"
    assert "never merges" in proposal["body"]

    recorded = audit_rows(storage)
    assert len(recorded) == 1
    assert recorded[0]["actor"] == "agent"
    assert recorded[0]["action"] == "proposal.deliver"
    assert recorded[0]["subject"] == "fp:3f9a1b2c4d5e6f70"


def test_failed_fix_result_is_not_delivered() -> None:
    storage = SQLiteStorage(":memory:")
    forge = ConsoleForge()

    result = FixProposalDelivery(storage, forge).deliver(
        make_fingerprint(), FixResult(False, "repro test passed before any patch"), RCA_SUMMARY
    )

    assert result == {"delivered": False, "reason": "repro test passed before any patch"}
    assert forge.proposals == []
    assert audit_rows(storage) == []


def test_body_contains_diff_confidence_and_rollback() -> None:
    body = format_proposal_body(RCA_SUMMARY, DIFF, make_fingerprint())

    assert "## Root cause" in body
    assert RCA_SUMMARY in body
    assert "```diff" in body
    assert 'return order.get("idempotency_key")' in body
    assert "Confidence:" in body
    assert "revert single commit" in body
    assert "Human review required — Wakey never merges." in body
