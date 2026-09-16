# SPDX-License-Identifier: Apache-2.0
"""Verification scheduler (E9-T1): runs the verification watcher on an interval.

Deliberately trivial: a bounded loop over an interval, host-safe (sleeps
between passes, hard max-rounds for tests). Crash-safety: an exception in
one pass is logged and the loop continues — verification must never take
the pipeline down (NFR-4).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from wakey.policy.watcher import VerificationWatcher

logger = logging.getLogger(__name__)


class VerificationScheduler:
    def __init__(
        self,
        watcher: VerificationWatcher,
        interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._watcher = watcher
        self._interval = interval_seconds
        self._clock = clock
        self._sleep = sleep

    def run_rounds(self, rounds: int) -> list[int]:
        """Run the watcher ``rounds`` times; returns evaluated counts per round."""
        counts: list[int] = []
        start = self._clock()
        for _ in range(rounds):
            try:
                results = self._watcher.run_once()
                counts.append(len(results))
            except Exception:
                logger.exception("verification watcher pass failed")
                counts.append(-1)
            start = self._pace(start)
        return counts

    def _pace(self, start: float) -> float:
        elapsed = self._clock() - start
        if elapsed < self._interval:
            self._sleep(self._interval - elapsed)
        return self._clock()
