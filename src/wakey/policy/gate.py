# SPDX-License-Identifier: Apache-2.0
"""Policy gate (WF-03 §7, DET-5/6): should this fingerprint wake anyone?

Pure function of (fingerprint, service config) → decision. No I/O, no time
windows yet — rate windows land with the burst-window store (E5-T3); until
then thresholds are count-based, which the demo and tests exercise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from wakey.core.config import Autonomy, ServiceYaml
from wakey.core.models import Fingerprint, WorkState


class Action(StrEnum):
    WAKE = "wake"  # create ticket / dispatch RCA per autonomy
    RECORD = "record"  # counters only, nothing fires
    ABSORBED = "absorbed"  # work already in flight (DET-14) — counts only
    SUPPRESSED = "suppressed"  # human dropped / closed it; stays quiet


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


def decide(fingerprint: Fingerprint, config: ServiceYaml) -> Decision:
    """The wake-up decision: severity × occurrences × state × autonomy."""
    if fingerprint.state.busy:
        return Decision(Action.ABSORBED, f"work already in flight ({fingerprint.state})")
    if fingerprint.state in (WorkState.DROPPED, WorkState.CLOSED_HUMAN):
        return Decision(Action.SUPPRESSED, f"fingerprint was {fingerprint.state} by a human")
    if config.autonomy == Autonomy.OBSERVE:
        return Decision(Action.RECORD, "observe mode: issues only, agents never fire")

    if fingerprint.severity.rank >= 4:  # critical wakes immediately
        return Decision(Action.WAKE, "critical severity")
    if fingerprint.occurrences >= config.immediate_count:
        return Decision(
            Action.WAKE,
            f"reached {fingerprint.occurrences} occurrences (threshold {config.immediate_count})",
        )
    return Decision(
        Action.RECORD, f"{fingerprint.occurrences}/{config.immediate_count} occurrences"
    )
