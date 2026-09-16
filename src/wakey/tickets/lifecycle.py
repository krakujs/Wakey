# SPDX-License-Identifier: Apache-2.0
"""Ticket lifecycle decisions (WF-04 §3-5, E6-T2/T3/T4).

Pure, clock-injected rules over open tickets:
- an OPEN/REOPENED ticket silent past the grace window auto-closes
- a recurrence on a CLOSED_AUTO ticket reopens it (never a new ticket)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from wakey.core.models import Fingerprint, WorkState


@dataclass(frozen=True)
class LifecycleDecision:
    action: str  # "comment" | "close" | "reopen" | "none"
    detail: str


def decide(
    fingerprint: Fingerprint,
    now: datetime,
    *,
    grace_minutes: int = 30,
) -> LifecycleDecision:
    """Single lifecycle rule application for one fingerprint at ``now``."""
    if fingerprint.ticket_url is None:
        return LifecycleDecision("none", "no ticket attached")

    silence = (now - fingerprint.last_seen).total_seconds() / 60 if fingerprint.last_seen else None

    if fingerprint.state in (WorkState.OPEN, WorkState.REOPENED, WorkState.INVESTIGATING):
        if silence is not None and silence >= grace_minutes:
            return LifecycleDecision("close", f"silent for {silence:.0f}m — auto-close")
        return LifecycleDecision(
            "none", f"active ({silence is None and 'no data' or f'{silence:.0f}m'})"
        )

    if fingerprint.state is WorkState.CLOSED_AUTO:
        return LifecycleDecision("reopen", "error recurred after auto-close")

    return LifecycleDecision("none", f"state {fingerprint.state.value} is terminal or queued")
