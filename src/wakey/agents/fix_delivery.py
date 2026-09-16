# SPDX-License-Identifier: Apache-2.0
"""Draft-proposal delivery (WF-06 §9-10): hand a proven fix to the forge.

The fix agent only proves a patch works; this module is the only place a
proven patch becomes a *draft* fix proposal, carrying everything a reviewer
needs — RCA summary, the unified diff, a confidence note, and the rollback
path. Wakey never merges (WF-15): the draft exists for human review, and
every delivery leaves an audit line. A fix result that did not succeed is
never delivered.
"""

from __future__ import annotations

from wakey.core.models import AuditEvent, Fingerprint
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort

NEVER_MERGES_NOTE = "Human review required — Wakey never merges."


def proposal_branch(fingerprint: Fingerprint) -> str:
    """Branch name for the fix attempt (WF-06 §2): ``wakey/fix-<hash8>``."""
    return f"wakey/fix-{fingerprint.fp_hash[:8]}"


def proposal_title(fingerprint: Fingerprint) -> str:
    """Draft-marked title so no reader can mistake the proposal for mergeable."""
    return f"[wakey] Fix for fp:{fingerprint.fp_hash} (draft)"


def format_proposal_body(rca_summary: str, diff: str, fingerprint: Fingerprint) -> str:
    """Reviewer-ready body (WF-06 §9, AC 8): RCA, diff, confidence, rollback."""
    return "\n".join(
        (
            f"Fingerprint: `fp:{fingerprint.fp_hash}` "
            f"({fingerprint.service}/{fingerprint.environment})",
            "",
            "## Root cause",
            rca_summary,
            "",
            "## Proposed change",
            "```diff",
            diff,
            "```",
            "",
            "Confidence: agent-generated patch — review it against the RCA above before approving.",
            "",
            "## Rollback",
            "revert single commit.",
            "",
            NEVER_MERGES_NOTE,
        )
    )


class FixProposalDelivery:
    """Delivers a completed :class:`~wakey.agents.fix.ReproFirstFixer` result as a draft.

    The fix result is typed ``object`` on purpose: delivery only duck-checks the
    three fields it needs (``ok``/``reason``/``diff``) so it stays decoupled from
    the fixer's iteration mechanics and accepts any result-shaped record.
    """

    def __init__(self, storage: Storage, forge: ForgePort) -> None:
        self._storage = storage
        self._forge = forge

    def deliver(
        self, fingerprint: Fingerprint, fix_result: object, rca_summary: str
    ) -> dict[str, object]:
        """Open a draft fix proposal on the forge and audit the delivery."""
        ok = bool(getattr(fix_result, "ok", False))
        if not ok:
            return {"delivered": False, "reason": str(getattr(fix_result, "reason", ""))}

        branch = proposal_branch(fingerprint)
        body = format_proposal_body(rca_summary, str(getattr(fix_result, "diff", "")), fingerprint)
        ref = self._forge.open_draft_proposal(branch, proposal_title(fingerprint), body)
        self._storage.record_audit(
            AuditEvent(
                actor="agent",
                action="proposal.deliver",
                subject=f"fp:{fingerprint.fp_hash}",
                details={"branch": branch, "url": ref.url},
            )
        )
        return {"branch": branch, "url": ref.url, "delivered": True}
