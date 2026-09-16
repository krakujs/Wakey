# SPDX-License-Identifier: Apache-2.0
"""Post-merge verification verdicts (WF-08, VER-1/2): did the fix actually fix it?

Pure decision function over watcher observations. The watcher (scheduler)
counts occurrences in the grace window after the fix ships and feeds the
inputs here; persistence and comment delivery live in WF-04/WF-08.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Verdict(StrEnum):
    SUCCESS = "success"
    INSUFFICIENT = "insufficient"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class WatchInputs:
    """What the watcher observed in the post-merge grace window."""

    occurrences_after_fix: int
    grace_window_elapsed: bool
    fix_deployed_to_environment: bool = True
    reverted_by_human: bool = False


def decide_verdict(watch: WatchInputs) -> Verdict:
    """The fix shipped, the window closed (or was cut short) — what happened?

    Rules (WF-08 §2-4): silence past the window = confirmed; continued
    occurrences = insufficient; a fix that never reached the emitting
    environment = inconclusive, not a verdict against the patch.
    """
    if watch.reverted_by_human:
        return Verdict.INCONCLUSIVE
    if not watch.fix_deployed_to_environment:
        return Verdict.INCONCLUSIVE
    if not watch.grace_window_elapsed:
        return Verdict.INCONCLUSIVE
    if watch.occurrences_after_fix == 0:
        return Verdict.SUCCESS
    return Verdict.INSUFFICIENT
