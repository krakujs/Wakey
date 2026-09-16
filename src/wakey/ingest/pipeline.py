# SPDX-License-Identifier: Apache-2.0
"""Ingest pipeline (E4-T1/E6-T1): event → fingerprint → policy → ticket.

The M0 skeleton of WF-02→WF-03→WF-04, one event at a time; the queue
(E2-T5) feeds it under load. Redaction already happened at ingest (SEC-1).
In-flight suppression falls out of the work-state machine: busy
fingerprints absorb occurrences and never re-ticket (DET-14).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from wakey.core.config import ServiceYaml
from wakey.core.models import AuditEvent, Fingerprint, LogEvent, WorkState
from wakey.core.storage import Storage
from wakey.fingerprints.engine import fingerprint_from_event
from wakey.forge.port import ForgePort, TicketRef
from wakey.policy.gate import Action, decide


class Outcome(StrEnum):
    """What happened to one event on its way through the pipeline."""

    TICKETED = "ticketed"  # new ticket created on this event
    ABSORBED = "absorbed"  # counted; work already in flight or ticket open
    RECORDED = "recorded"  # counted; below thresholds
    SUPPRESSED = "suppressed"  # human decision keeps it quiet


@dataclass(frozen=True)
class Result:
    fingerprint_hash: str
    outcome: Outcome
    detail: str


class IngestPipeline:
    """Processes one event at a time; the queue feeds it under load."""

    def __init__(self, storage: Storage, forge: ForgePort) -> None:
        self._storage = storage
        self._forge = forge

    def handle_event(self, event: LogEvent) -> Result:
        fingerprint = fingerprint_from_event(event)
        stored = self._storage.record_occurrence(fingerprint, delta=1)
        decision = decide(stored, ServiceYaml())  # per-repo config sync lands with E3-T6

        if decision.action in (Action.ABSORBED, Action.SUPPRESSED):
            outcome = Outcome.ABSORBED if decision.action is Action.ABSORBED else Outcome.SUPPRESSED
            return Result(stored.fp_hash, outcome, decision.reason)
        if decision.action is Action.RECORD:
            return Result(stored.fp_hash, Outcome.RECORDED, decision.reason)

        if stored.ticket_url is None:
            return self._create_ticket(stored, decision.reason)
        self._forge.add_comment(_ticket_ref(stored), f"still occurring ×{stored.occurrences}")
        return Result(stored.fp_hash, Outcome.ABSORBED, "open ticket updated")

    def _create_ticket(self, stored: Fingerprint, reason: str) -> Result:
        ref = self._forge.create_ticket(stored, _title(stored), _body(stored, reason))
        opened = stored.model_copy(
            update={"state": WorkState.OPEN, "ticket_issue_id": ref.issue_id, "ticket_url": ref.url}
        )
        self._storage.save_fingerprint(opened)
        self._storage.record_audit(
            AuditEvent(
                actor="system",
                action="ticket.create",
                subject=f"fp:{stored.fp_hash}",
                details={"forge_issue": ref.issue_id, "reason": reason},
            )
        )
        return Result(stored.fp_hash, Outcome.TICKETED, reason)


def _title(fingerprint: Fingerprint) -> str:
    return (
        f"[wakey] {fingerprint.service}: {fingerprint.template[:80]} (×{fingerprint.occurrences})"
    )


def _body(fingerprint: Fingerprint, reason: str) -> str:
    frames = "\n".join(f"  - {f.path}:{f.line} in {f.function}" for f in fingerprint.frames)
    frame_block = f"\n\n**Top frames**\n{frames}" if frames else ""
    return (
        f"**Fingerprint** `fp:{fingerprint.fp_hash}` · **Occurrences** ×{fingerprint.occurrences}"
        f" · **Environment** {fingerprint.environment}\n\n"
        f"**Template** `{fingerprint.template}`{frame_block}\n\n"
        f"**Woke because**: {reason}\n\n"
        "RCA follows once the agent investigates (autonomy: triage)."
    )


def _ticket_ref(fingerprint: Fingerprint) -> TicketRef:
    return TicketRef(
        issue_id=fingerprint.ticket_issue_id or fingerprint.fp_hash,
        url=fingerprint.ticket_url or "",
    )
