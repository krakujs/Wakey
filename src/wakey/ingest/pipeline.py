# SPDX-License-Identifier: Apache-2.0
"""Ingest pipeline (E4-T1/E6-T1): event → fingerprint → policy → ticket.

The M0 skeleton of WF-02→WF-03→WF-04, one event at a time; the delivery
worker (R-02) feeds it after durable persistence. Redaction already
happened at ingest (SEC-1, two passes).

Policy enforcement (R-05): the gate applies the *stored* per-service
config (E3-T6), severity filtering, and the sliding-window rate counter;
comments on an open ticket are throttled to doubling occurrences so a
burst cannot flood the forge; ticket creation reserves dispatch ownership
(DET-14: fingerprint leaves NEW before the external call) and obeys a
per-service hourly cap.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import ValidationError

from wakey.core.config import Autonomy, ServiceYaml
from wakey.core.models import AuditEvent, Fingerprint, LogEvent, WorkState, utcnow
from wakey.core.storage import Storage
from wakey.fingerprints.engine import fingerprint_from_event
from wakey.fingerprints.windows import SlidingWindowCounters
from wakey.forge.port import ForgePort, TicketRef
from wakey.policy.gate import Action, decide

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    """What happened to one event on its way through the pipeline."""

    TICKETED = "ticketed"  # new ticket created on this event
    ABSORBED = "absorbed"  # counted; work already in flight or ticket open
    RECORDED = "recorded"  # counted; below thresholds / caps / severity floor
    SUPPRESSED = "suppressed"  # human decision keeps it quiet


@dataclass(frozen=True)
class Result:
    fingerprint_hash: str
    outcome: Outcome
    detail: str


class IngestPipeline:
    """Processes one event at a time; the delivery worker feeds it."""

    def __init__(
        self,
        storage: Storage,
        forge: ForgePort,
        counters: SlidingWindowCounters | None = None,
        ticket_counters: SlidingWindowCounters | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._forge = forge
        self._counters = counters if counters is not None else SlidingWindowCounters()
        self._ticket_counters = (
            ticket_counters if ticket_counters is not None else SlidingWindowCounters(3600)
        )
        self._clock = clock

    def handle_event(self, event: LogEvent) -> Result:
        fingerprint = fingerprint_from_event(event)
        stored = self._storage.record_occurrence(fingerprint, delta=1)
        config = self._config(event.service)
        now = self._clock()
        self._counters.record(stored.fp_hash, now.timestamp())
        decision = decide(
            stored,
            config,
            rate_in_window=self._counters.count_in_window(stored.fp_hash, now.timestamp()),
        )

        if decision.action is Action.RECORD:
            return Result(stored.fp_hash, Outcome.RECORDED, decision.reason)
        if decision.action is Action.ABSORBED:
            return Result(stored.fp_hash, Outcome.ABSORBED, decision.reason)
        if decision.action is Action.SUPPRESSED:
            return Result(stored.fp_hash, Outcome.SUPPRESSED, decision.reason)

        if stored.ticket_url is None:
            return self._create_ticket(stored, config, decision.reason)
        return self._update_ticket(stored)

    # --- configuration ---------------------------------------------------------

    def _config(self, service_name: str) -> ServiceYaml:
        """Stored per-service config; defaults when unregistered/invalid (E3-T6)."""
        stored = self._storage.get_service(service_name)
        if stored is None:
            return ServiceYaml()
        try:
            return ServiceYaml.model_validate(json.loads(stored.config_json))
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "service %s has invalid stored config, using defaults: %s", service_name, exc
            )
            return ServiceYaml()

    # --- ticket creation ---------------------------------------------------------

    def _create_ticket(self, stored: Fingerprint, config: ServiceYaml, reason: str) -> Result:
        now = self._clock()
        self._ticket_counters.record(f"tickets:{stored.service}", now.timestamp())
        open_tickets = self._ticket_counters.count_in_window(
            f"tickets:{stored.service}", now.timestamp()
        )
        if open_tickets > config.max_tickets_per_hour:
            return Result(
                stored.fp_hash,
                Outcome.RECORDED,
                f"service ticket cap reached ({config.max_tickets_per_hour}/h) — counted only",
            )

        # Dispatch ownership before any external call (DET-14): a crash or
        # retry now sees a busy fingerprint instead of double-ticketing.
        reserved = stored.model_copy(
            update={
                "state": WorkState.QUEUED_RCA
                if config.autonomy is not Autonomy.OBSERVE
                else WorkState.OPEN
            }
        )
        self._storage.save_fingerprint(reserved)
        try:
            ref = self._forge.create_ticket(stored, _title(stored), _body(stored, reason))
        except Exception:
            # release the reservation so the delivery retry can ticket again
            self._storage.save_fingerprint(stored.model_copy(update={"state": WorkState.NEW}))
            raise
        ticketed = reserved.model_copy(
            update={"ticket_issue_id": ref.issue_id, "ticket_url": ref.url}
        )
        self._storage.save_fingerprint(ticketed)
        self._storage.record_audit(
            AuditEvent(
                actor="system",
                action="ticket.create",
                subject=f"fp:{stored.fp_hash}",
                details={"forge_issue": ref.issue_id, "reason": reason},
            )
        )
        return Result(stored.fp_hash, Outcome.TICKETED, reason)

    # --- comment throttling --------------------------------------------------------

    def _update_ticket(self, stored: Fingerprint) -> Result:
        """Comment (throttled) or reopen on recurrence after auto-close (E6-T3)."""
        if stored.state is WorkState.CLOSED_AUTO:
            reopened = stored.model_copy(update={"state": WorkState.REOPENED})
            self._storage.save_fingerprint(reopened)
            self._forge.reopen_ticket(
                _ticket_ref(stored),
                f"error recurred ×{stored.occurrences} after auto-close — reopening (WF-04).",
            )
            return Result(stored.fp_hash, Outcome.ABSORBED, "auto-closed ticket reopened")

        floor = max(stored.commented_at_occurrences * 2, 4)
        if stored.occurrences < floor:
            return Result(stored.fp_hash, Outcome.ABSORBED, "open ticket; comment throttled")
        self._forge.add_comment(_ticket_ref(stored), f"still occurring ×{stored.occurrences}")
        updated = stored.model_copy(
            update={
                "commented_at_occurrences": stored.occurrences,
                "last_comment_at": self._clock(),
            }
        )
        self._storage.save_fingerprint(updated)
        return Result(stored.fp_hash, Outcome.ABSORBED, "open ticket updated")


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
