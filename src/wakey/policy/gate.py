# SPDX-License-Identifier: Apache-2.0
"""Policy gate (WF-03 §7, DET-5/6): should this fingerprint wake anyone?

Pure function of (fingerprint, service config, window rate) → decision.
No I/O. Error filtering is enforced here (R-05): only error-severity
fingerprints can ever wake — an INFO storm is recorded, never ticketed.

Autonomy semantics (resolved 2026-09-16, WF-03 §7 ↔ WF-07 §6 conflict):
``observe`` still emits the wake signal so the ticket exists for humans,
but no agent may dispatch (RCA/fix). Agent dispatch checks autonomy, not
this gate. WF-03's acceptance criterion (agent/LLM counters stay zero)
matches WF-07's definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from wakey.core.config import ServiceYaml
from wakey.core.models import Fingerprint, Severity, WorkState


class Action(StrEnum):
    WAKE = "wake"  # create ticket / dispatch RCA per autonomy
    RECORD = "record"  # counters only, nothing fires
    ABSORBED = "absorbed"  # work already in flight (DET-14) — counts only
    SUPPRESSED = "suppressed"  # human dropped / closed it; stays quiet


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def decide(
    fingerprint: Fingerprint,
    config: ServiceYaml,
    rate_in_window: int = 0,
) -> Decision:
    """The wake-up decision: severity × occurrences × window × state."""
    if fingerprint.severity.rank < Severity.ERROR.rank:
        return Decision(
            Action.RECORD,
            f"{fingerprint.severity.value} severity is below the error wake floor",
        )
    if fingerprint.state.busy:
        return Decision(Action.ABSORBED, f"work already in flight ({fingerprint.state})")
    if fingerprint.state in (WorkState.DROPPED, WorkState.CLOSED_HUMAN):
        return Decision(Action.SUPPRESSED, f"fingerprint was {fingerprint.state} by a human")

    reason = _wake_reason(fingerprint, config, rate_in_window)
    if reason is not None:
        return Decision(Action.WAKE, reason)
    return Decision(
        Action.RECORD, f"{fingerprint.occurrences}/{config.immediate_count} occurrences"
    )


def _wake_reason(fingerprint: Fingerprint, config: ServiceYaml, rate_in_window: int) -> str | None:
    """A non-None reason means the fingerprint should wake."""
    if fingerprint.severity.rank >= 4:  # critical wakes immediately
        return "critical severity"
    if fingerprint.occurrences >= config.immediate_count:
        return f"reached {fingerprint.occurrences} occurrences (threshold {config.immediate_count})"
    if rate_in_window >= config.rate_per_min:
        return f"burst: {rate_in_window} errors in the rate window (cap {config.rate_per_min})"
    return None
