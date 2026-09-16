# SPDX-License-Identifier: Apache-2.0
"""Verification watcher (WF-08, E9-T1): applies verdicts and closes the loop.

``run_once`` is one pass: for every fingerprint in ``verifying`` state,
build WatchInputs via the injected grace-check callable, decide the
verdict, and apply it — locally *and* on the forge (R-09): success closes
the ticket with a ``wakey-verified`` label, insufficiency reopens it,
labels ``wakey-recurred``, and marks the fingerprint chronic so the FIX-1
gate refuses any further autonomous fix without human acknowledgement.
All decisions audited; the verification window start is persisted so a
restart resumes the same window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from wakey.core.models import AuditEvent, Fingerprint, WorkState
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort, TicketRef
from wakey.policy.verification import Verdict, WatchInputs, decide_verdict

LABEL_VERIFIED = "wakey-verified"
LABEL_RECURRED = "wakey-recurred"


@dataclass(frozen=True)
class WatchResult:
    fp_hash: str
    verdict: Verdict


class VerificationWatcher:
    """One pass = evaluate every fingerprint in ``verifying`` state."""

    def __init__(
        self,
        storage: Storage,
        forge: ForgePort,
        grace_inputs: Callable[[Fingerprint], WatchInputs],
    ) -> None:
        self._storage = storage
        self._forge = forge
        self._grace_inputs = grace_inputs

    def run_once(self) -> list[WatchResult]:
        results: list[WatchResult] = []
        for fingerprint in self._storage.list_active_fingerprints():
            if fingerprint.state is not WorkState.VERIFYING:
                continue
            watch = self._grace_inputs(fingerprint)
            verdict = decide_verdict(watch)
            self._apply(fingerprint, verdict, watch)
            results.append(WatchResult(fingerprint.fp_hash, verdict))
        return results

    def _apply(self, fingerprint: Fingerprint, verdict: Verdict, watch: WatchInputs) -> None:
        updates: dict[str, object]
        if verdict is Verdict.SUCCESS:
            updates = {"state": WorkState.VERIFIED_CLOSED}
        elif verdict is Verdict.INSUFFICIENT:
            # chronic flag = repeat-fix inhibition: FIX-1 refuses autonomous
            # fixes for this fingerprint until a human acknowledges (VER-4)
            updates = {"state": WorkState.REOPENED, "chronic": True}
        else:
            updates = {}  # inconclusive: stays verifying, retried later

        # External lifecycle effects only exist when a ticket exists (R-09)
        if fingerprint.ticket_url is not None:
            ticket = TicketRef(
                issue_id=fingerprint.ticket_issue_id or fingerprint.fp_hash,
                url=fingerprint.ticket_url,
            )
            if verdict is Verdict.SUCCESS:
                self._forge.close_ticket(
                    ticket,
                    f"fix verified: {watch.occurrences_after_fix} occurrences in grace window",
                )
                self._forge.add_label(ticket, LABEL_VERIFIED)
            elif verdict is Verdict.INSUFFICIENT:
                self._forge.reopen_ticket(
                    ticket,
                    f"error recurred ×{watch.occurrences_after_fix} after the fix — "
                    "reopening; further autonomous fixes are blocked pending human review",
                )
                self._forge.add_label(ticket, LABEL_RECURRED)

        stored = fingerprint.model_copy(update=dict(updates))
        self._storage.save_fingerprint(stored)
        self._storage.record_audit(
            AuditEvent(
                actor="system",
                action=f"verify.{verdict.value}",
                subject=f"fp:{fingerprint.fp_hash}",
                details={"occurrences": str(watch.occurrences_after_fix)},
            )
        )


def grace_inputs_from_storage(
    fingerprint: Fingerprint,
    now_seconds: Callable[[], float],
    grace_minutes: int,
    deployed: Callable[[Fingerprint], bool] | None = None,
) -> WatchInputs:
    """Build WatchInputs from persisted verification anchors (R-09).

    The window starts at ``verification_started_at`` (persisted when the
    fingerprint entered ``verifying``), so a restart resumes the same
    window; occurrences are counted from the level frozen at window start.
    """
    started = fingerprint.verification_started_at
    if started is None:
        return WatchInputs(
            occurrences_after_fix=0,
            grace_window_elapsed=False,
            fix_deployed_to_environment=False,
        )
    elapsed = (now_seconds() - started.timestamp()) / 60.0 >= grace_minutes
    is_deployed = deployed(fingerprint) if deployed is not None else True
    return WatchInputs(
        occurrences_after_fix=max(
            0, fingerprint.occurrences - fingerprint.verification_start_occurrences
        ),
        grace_window_elapsed=elapsed,
        fix_deployed_to_environment=is_deployed,
    )
