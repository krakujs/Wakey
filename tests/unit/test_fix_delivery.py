# SPDX-License-Identifier: Apache-2.0
"""FixProposalDelivery tests (WF-06 §9-10): draft delivery, refusal, audit line."""

from __future__ import annotations

import sqlite3

from wakey.agents.fix import FixResult
from wakey.agents.fix_delivery import FixProposalDelivery, format_proposal_body
from wakey.core.models import Fingerprint, Severity, WorkState
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

    FILES = {"billing.py": 'def get_idempotency_key(order):\n    return order.get("k")\n'}
    result = FixProposalDelivery(storage, forge).deliver(
        fingerprint,
        FixResult(True, "repro green after 1 patch attempt(s)", DIFF, 1, FILES),
        RCA_SUMMARY,
    )

    assert result == {
        "branch": "wakey/fix-3f9a1b2c",
        "commit": "console-sha-wakey/fix-3f9a1b2c",
        "url": "console://pulls/wakey/fix-3f9a1b2c",
        "delivered": True,
    }
    assert len(forge.proposals) == 1
    proposal = forge.proposals[0]
    assert proposal["branch"] == "wakey/fix-3f9a1b2c"
    assert proposal["base"] == "main"
    assert proposal["title"] == "[wakey] Fix for fp:3f9a1b2c4d5e6f70 (draft)"
    assert "never merges" in proposal["body"]
    # R-06: the tested tree is committed, not just described
    assert forge.branches["wakey/fix-3f9a1b2c"] == FILES
    # binding persisted for the verification lifecycle
    stored = storage.get_fingerprint("3f9a1b2c4d5e6f70")
    assert stored is not None

    assert stored.state is WorkState.AWAITING_HUMAN
    assert stored.proposal_branch == "wakey/fix-3f9a1b2c"
    assert stored.proposal_url == "console://pulls/wakey/fix-3f9a1b2c"

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


def test_result_without_files_is_never_delivered() -> None:
    """A PR body containing a diff is not proof of code delivery (R-06)."""
    storage = SQLiteStorage(":memory:")
    forge = ConsoleForge()
    result = FixProposalDelivery(storage, forge).deliver(
        make_fingerprint(), FixResult(True, "ok", DIFF, 1, files={}), RCA_SUMMARY
    )
    assert result == {"delivered": False, "reason": "no changed files to publish"}
    assert forge.proposals == []


def test_branch_collision_suffixes_instead_of_failing() -> None:
    storage = SQLiteStorage(":memory:")

    class CollidingForge(ConsoleForge):
        def __init__(self) -> None:
            super().__init__()
            self.branches["wakey/fix-3f9a1b2c"] = {}  # earlier attempt left it behind

        def create_branch(self, branch: str, base: str) -> None:
            if branch in self.branches:
                raise RuntimeError("reference already exists")
            self.branches[branch] = {}

    result = FixProposalDelivery(storage, CollidingForge()).deliver(
        make_fingerprint(),
        FixResult(True, "ok", DIFF, 1, files={"billing.py": "x"}),
        RCA_SUMMARY,
    )
    assert result["delivered"] is True
    assert result["branch"] == "wakey/fix-3f9a1b2c-2"


def test_body_contains_diff_confidence_and_rollback() -> None:
    body = format_proposal_body(RCA_SUMMARY, DIFF, make_fingerprint())

    assert "## Root cause" in body
    assert RCA_SUMMARY in body
    assert "```diff" in body
    assert 'return order.get("idempotency_key")' in body
    assert "Confidence:" in body
    assert "revert single commit" in body
    assert "Human review required — Wakey never merges." in body
