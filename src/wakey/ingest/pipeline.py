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
import threading
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
from wakey.forge.port import ForgePort, TicketRef, is_ambiguous_write
from wakey.policy.gate import Action, decide

logger = logging.getLogger(__name__)

# A ticket-creation reservation older than this with no ticket refs means
# the creating worker died before the forge answered — recover it (DET-14).
_TICKET_CREATE_STALE_SECONDS = 120


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
        self._dispatch_lock = threading.Lock()

    def handle_event(self, event: LogEvent) -> Result:
        # The composed instance owns one pipeline. Serialize policy+forge effects
        # across its delivery workers, not merely individual database writes.
        with self._dispatch_lock:
            return self._handle_event(event)

    def _handle_event(self, event: LogEvent) -> Result:
        fingerprint = fingerprint_from_event(event)
        # Exactly-once counting (R-02): event persistence, the per-event
        # counted flag, and the occurrence increment commit atomically.
        # Policy execution is journaled separately at the end of this
        # method — a forge failure or crash before that replays the
        # *policy* on retry (the state machine absorbs re-runs, DET-14)
        # without re-counting.
        stored = self._storage.claim_event(event, fingerprint)
        if self._storage.is_event_dispatched(event.id):
            # Policy already applied on a previous attempt (R-02): nothing
            # to re-run, and the count was already claimed (delta 0).
            return Result(
                stored.fp_hash, Outcome.ABSORBED, "event already dispatched — policy applied"
            )
        stored = self._recover_interrupted_reservation(stored)
        config = self._config(event.service)
        now = self._clock()
        self._counters.record(stored.fp_hash, now.timestamp())
        decision = decide(
            stored,
            config,
            rate_in_window=self._counters.count_in_window(stored.fp_hash, now.timestamp()),
        )

        if decision.action is Action.RECORD:
            result = Result(stored.fp_hash, Outcome.RECORDED, decision.reason)
        elif decision.action is Action.ABSORBED:
            result = Result(stored.fp_hash, Outcome.ABSORBED, decision.reason)
        elif decision.action is Action.SUPPRESSED:
            result = Result(stored.fp_hash, Outcome.SUPPRESSED, decision.reason)
        elif stored.ticket_url is None:
            result = self._create_ticket(stored, config, decision.reason)
        else:
            result = self._update_ticket(stored)
        # Policy finished — journal dispatch so completed events never
        # re-enter the pipeline (they are still re-countable-proof anyway).
        self._storage.mark_event_dispatched(event.id)
        return result

    def _recover_interrupted_reservation(self, stored: Fingerprint) -> Fingerprint:
        """Re-arm a reservation whose ticket creation never completed (DET-14).

        ``queued-rca`` (or observe-mode ``open``) with no ticket refs means
        a worker reserved the fingerprint and died (or lost the forge
        response) before the ticket bound. A *fresh* reservation is in
        flight in another worker and stays absorbed; a stale one is
        reconciled against the remote first (R-15) — an issue GitHub did
        accept is bound, and only a confirmed absence returns the
        fingerprint to ``new`` for the wake path below.
        """
        if stored.ticket_url is not None or stored.reserved_at is None:
            return stored
        if stored.state not in (WorkState.QUEUED_RCA, WorkState.OPEN):
            return stored
        now = self._clock()
        if (now - stored.reserved_at).total_seconds() < _TICKET_CREATE_STALE_SECONDS:
            return stored
        reconciled = self._reconcile_remote_ticket(stored)
        if reconciled is not None:
            return self._bind_ticket(
                stored,
                reconciled,
                reason="recovered interrupted ticket creation",
                reconciled=True,
            )
        recovered = stored.model_copy(update={"state": WorkState.NEW, "reserved_at": None})
        self._storage.save_fingerprint(recovered)
        return recovered

    def _bind_ticket(
        self, reserved: Fingerprint, ref: TicketRef, reason: str, reconciled: bool
    ) -> Fingerprint:
        """Persist the fingerprint↔ticket binding (state preserved as reserved)."""
        bound = reserved.model_copy(
            update={
                "ticket_issue_id": ref.issue_id,
                "ticket_url": ref.url,
                "reserved_at": None,
            }
        )
        self._storage.save_fingerprint(bound)
        self._storage.record_audit(
            AuditEvent(
                actor="system",
                action="ticket.create",
                subject=f"fp:{reserved.fp_hash}",
                details={
                    "forge_issue": ref.issue_id,
                    "reason": reason,
                    "reconciled": str(reconciled).lower(),
                },
            )
        )
        return bound

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
        # ``reserved_at`` timestamps the intent so the interrupted-
        # reservation recovery can distinguish in-flight work from a dead
        # worker whose forge call never completed.
        reserved = stored.model_copy(
            update={
                "state": WorkState.QUEUED_RCA
                if config.autonomy is not Autonomy.OBSERVE
                else WorkState.OPEN,
                "reserved_at": now,
            }
        )
        self._storage.save_fingerprint(reserved)
        ref: TicketRef | None = None
        reconciled = False
        try:
            ref = self._forge.create_ticket(stored, _title(stored), _body(stored, reason))
        except Exception as exc:
            # Reconciliation before re-creation (R-15): an ambiguous failure
            # (lost response, 5xx) may mean GitHub already accepted the
            # issue — bind it instead of creating a twin. A definitive
            # rejection wrote nothing, so the reservation is released for
            # the delivery retry to ticket again.
            ambiguous = is_ambiguous_write(exc)
            if ambiguous:
                ref = self._reconcile_remote_ticket(stored)
            if ref is None:
                if ambiguous:
                    # Keep the reservation: the retry would otherwise race a
                    # possibly-accepted create. The stale-reservation pass
                    # re-reconciles before re-arming the wake path.
                    logger.warning(
                        "ambiguous ticket create for fp:%s — reservation kept", stored.fp_hash
                    )
                else:
                    self._storage.save_fingerprint(
                        stored.model_copy(update={"state": WorkState.NEW, "reserved_at": None})
                    )
                raise
            reconciled = True
        self._bind_ticket(reserved, ref, reason, reconciled)
        return Result(stored.fp_hash, Outcome.TICKETED, reason)

    def _reconcile_remote_ticket(self, stored: Fingerprint) -> TicketRef | None:
        """Look up a ticket this fingerprint already created (R-15)."""
        try:
            return self._forge.find_ticket_by_marker(f"fp:{stored.fp_hash}")
        except Exception:  # noqa: BLE001 — lookup failure leaves the retry to decide
            logger.warning("ticket reconciliation lookup failed for fp:%s", stored.fp_hash)
            return None

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
