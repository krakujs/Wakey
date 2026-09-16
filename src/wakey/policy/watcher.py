# SPDX-License-Identifier: Apache-2.0
"""Verification watcher (WF-08, E9-T1): applies verdicts to verifying tickets.

Scheduling lives with the caller (cron/loop); ``run_once`` is one pass:
for every fingerprint in ``verifying`` state, build WatchInputs via the
injected grace-check callable, decide the verdict, and apply it —
success closes the ticket, insufficient reopens it with the chronic
guard engaged. All decisions audited.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from wakey.core.models import AuditEvent, Fingerprint, WorkState
from wakey.core.storage import Storage
from wakey.policy.verification import Verdict, WatchInputs, decide_verdict


@dataclass(frozen=True)
class WatchResult:
    fp_hash: str
    verdict: Verdict


class VerificationWatcher:
    """One pass = evaluate every fingerprint in ``verifying`` state."""

    def __init__(
        self,
        storage: Storage,
        forge: object,
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
        if verdict is Verdict.SUCCESS:
            updated = fingerprint.model_copy(update={"state": WorkState.VERIFIED_CLOSED})
            self._storage.save_fingerprint(updated)
        elif verdict is Verdict.INSUFFICIENT:
            updated = fingerprint.model_copy(update={"state": WorkState.REOPENED})
            self._storage.save_fingerprint(updated)
        else:
            updated = fingerprint  # inconclusive: stays verifying, retried later
        self._storage.record_audit(
            AuditEvent(
                actor="system",
                action=f"verify.{verdict.value}",
                subject=f"fp:{fingerprint.fp_hash}",
                details={"occurrences": str(watch.occurrences_after_fix)},
            )
        )
