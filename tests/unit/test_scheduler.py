# SPDX-License-Identifier: Apache-2.0
"""Scheduler tests: paces passes, survives crashes, reports counts."""

from __future__ import annotations

from pathlib import Path

from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.policy.scheduler import VerificationScheduler
from wakey.policy.verification import WatchInputs
from wakey.policy.watcher import VerificationWatcher


def test_rounds_and_crash_tolerance(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(
        Fingerprint(
            fp_hash="aaaaaaaaaaaaaaaa",
            service="api",
            environment="prod",
            template="boom",
            severity=Severity.ERROR,
            state=WorkState.VERIFYING,
        )
    )

    def grace_inputs(fp: Fingerprint) -> WatchInputs:
        return WatchInputs(
            occurrences_after_fix=0, grace_window_elapsed=True, fix_deployed_to_environment=True
        )

    watcher = VerificationWatcher(storage, forge=None, grace_inputs=grace_inputs)

    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_clock() -> float:
        return clock["t"]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += max(seconds, 0.001)

    scheduler = VerificationScheduler(
        watcher, interval_seconds=10, clock=fake_clock, sleep=fake_sleep
    )
    counts = scheduler.run_rounds(rounds=2)

    # round 1 verifies the fingerprint (-> closed); round 2 has nothing left
    assert counts == [1, 0]
    assert len(sleeps) == 2
    assert all(s > 0 for s in sleeps)


def test_crash_in_pass_reported_not_fatal(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")

    class ExplodingWatcher(VerificationWatcher):
        def run_once(self) -> list:
            raise RuntimeError("boom")

    def inputs_for(fp: Fingerprint) -> WatchInputs:
        return WatchInputs(occurrences_after_fix=0, grace_window_elapsed=True)

    scheduler = VerificationScheduler(
        ExplodingWatcher(storage, forge=None, grace_inputs=inputs_for),
        interval_seconds=1,
    )
    counts = scheduler.run_rounds(rounds=2)
    assert counts == [-1, -1]
