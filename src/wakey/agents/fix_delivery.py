# SPDX-License-Identifier: Apache-2.0
"""Draft-proposal delivery (WF-06 §9-10): a proven patch becomes a real branch.

The fix agent only proves a patch works; this module publishes it:
resolve the forge's default branch, create an isolated ``wakey/fix-*``
branch, **commit the tested files** (R-06 — a diff in a PR body is not
code delivery), open a draft PR against that base, and persist the
fingerprint↔issue↔branch binding. Wakey never merges (WF-15); every
delivery leaves an audit line; a fix result without changed files or one
that did not succeed is never delivered.
"""

from __future__ import annotations

import logging

from wakey.core.models import AuditEvent, Fingerprint, WorkState
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort

logger = logging.getLogger(__name__)

NEVER_MERGES_NOTE = "Human review required — Wakey never merges."

MAX_BRANCH_ATTEMPTS = 3


def proposal_branch(fingerprint: Fingerprint, attempt: int = 1) -> str:
    """Branch name for the fix attempt (WF-06 §2): ``wakey/fix-<hash8>``."""
    stem = f"wakey/fix-{fingerprint.fp_hash[:8]}"
    return stem if attempt == 1 else f"{stem}-{attempt}"


def _bound_attempt(branch: str | None, fingerprint: Fingerprint) -> int:
    """Which suffix a persisted branch name corresponds to (1 for the stem)."""
    if not branch:
        return 1
    stem = proposal_branch(fingerprint, 1)
    if branch == stem:
        return 1
    if branch.startswith(f"{stem}-"):
        suffix = branch[len(stem) + 1 :]
        if suffix.isdigit():
            return int(suffix)
    return 1


def proposal_title(fingerprint: Fingerprint) -> str:
    """Draft-marked title so no reader can mistake the proposal for mergeable."""
    return f"[wakey] Fix for fp:{fingerprint.fp_hash} (draft)"


def format_proposal_body(
    rca_summary: str,
    diff: str,
    fingerprint: Fingerprint,
    *,
    test_summary: str = "Sandboxed repository test command passed.",
    verifier_reason: str = "",
) -> str:
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
            "## Tests",
            test_summary,
            "",
            "Confidence: agent-generated patch — review it against the RCA above before approving.",
            "",
            "## Rollback",
            "revert single commit.",
            "",
            NEVER_MERGES_NOTE,
            f"<!-- wakey-verifier: pass; {verifier_reason} -->" if verifier_reason else "",
        )
    )


class FixProposalDelivery:
    """Delivers a completed :class:`~wakey.agents.fix.ReproFirstFixer` result.

    The fix result is typed ``object`` on purpose: delivery only duck-checks
    the fields it needs (``ok``/``reason``/``diff``/``files``) so it stays
    decoupled from the fixer's iteration mechanics.
    """

    def __init__(self, storage: Storage, forge: ForgePort) -> None:
        self._storage = storage
        self._forge = forge

    def deliver(
        self,
        fingerprint: Fingerprint,
        fix_result: object,
        rca_summary: str,
        *,
        base_ref: str | None = None,
        test_summary: str = "Sandboxed repository test command passed.",
        verifier_reason: str = "",
        untested: bool = False,
    ) -> dict[str, object]:
        """Publish the tested patch to a draft branch/PR and audit the delivery."""
        ok = bool(getattr(fix_result, "ok", False))
        if not ok:
            return {"delivered": False, "reason": str(getattr(fix_result, "reason", ""))}

        files: dict[str, str] = dict(getattr(fix_result, "files", {}) or {})
        if not files:
            # R-06: a PR body containing a diff is not proof of code delivery
            return {"delivered": False, "reason": "no changed files to publish"}

        base = self._forge.default_branch()
        # Persist the branch binding BEFORE any external effect (R-15): a
        # crash after the branch/PR exist but before the binding is stored
        # must resume this delivery, not publish a twin proposal. Adapters
        # make the branch/PR effects idempotent per branch name.
        branch = self._ensure_branch(fingerprint, base_ref or base)
        sha = self._forge.commit_files(
            branch,
            f"wakey: suggested fix for fp:{fingerprint.fp_hash}",
            files,
        )
        body = format_proposal_body(
            rca_summary,
            str(getattr(fix_result, "diff", "")),
            fingerprint,
            test_summary=test_summary,
            verifier_reason=verifier_reason,
        )
        body += f"\n\n**Published commit**: `{sha}` on `{branch}` (base `{base}`)\n"
        ref = self._forge.open_draft_proposal(branch, proposal_title(fingerprint), body, base=base)
        self._forge.add_label(ref, "wakey/fix")
        if untested:
            self._forge.add_label(ref, "wakey/untested")

        updated = fingerprint.model_copy(
            update={
                "state": WorkState.AWAITING_HUMAN,
                "proposal_issue_id": ref.issue_id,
                "proposal_url": ref.url,
                "proposal_branch": branch,
            }
        )
        self._storage.save_fingerprint(updated)
        self._storage.record_audit(
            AuditEvent(
                actor="agent",
                action="proposal.deliver",
                subject=f"fp:{fingerprint.fp_hash}",
                details={
                    "branch": branch,
                    "commit": sha,
                    "url": ref.url,
                    "untested": str(untested).lower(),
                    "verifier": verifier_reason,
                },
            )
        )
        return {"branch": branch, "commit": sha, "url": ref.url, "delivered": True}

    def _ensure_branch(self, fingerprint: Fingerprint, base_ref: str) -> str:
        """Bind a delivery branch, resuming the persisted intent (R-15).

        The first candidate is whatever a previous crashed attempt already
        persisted; suffixing only advances on real name collisions so a
        retried delivery never orphans its committed branch.
        """
        start = _bound_attempt(fingerprint.proposal_branch, fingerprint)
        last_error: Exception | None = None
        for attempt in range(start, MAX_BRANCH_ATTEMPTS + 1):
            name = proposal_branch(fingerprint, attempt)
            try:
                self._forge.create_branch(name, base_ref)
            except Exception as exc:  # collision or forge error — try next suffix
                last_error = exc
                logger.warning("branch %s unavailable (%s), trying next suffix", name, exc)
                continue
            if name != fingerprint.proposal_branch:
                self._storage.save_fingerprint(
                    fingerprint.model_copy(update={"proposal_branch": name})
                )
            return name
        assert last_error is not None
        raise last_error
