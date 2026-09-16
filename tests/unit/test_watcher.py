# SPDX-License-Identifier: Apache-2.0
"""Watcher pass tests (E9-T1 + R-09): verdicts drive local AND forge state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.policy.verification import Verdict, WatchInputs
from wakey.policy.watcher import (
    LABEL_RECURRED,
    LABEL_VERIFIED,
    VerificationWatcher,
    grace_inputs_from_storage,
)


def make_fp(fp_hash: str, state: WorkState) -> Fingerprint:
    return Fingerprint(
        fp_hash=fp_hash,
        service="payments-api",
        environment="prod",
        template="boom",
        severity=Severity.ERROR,
        state=state,
        ticket_url="console://tickets/1",
        ticket_issue_id="console-1",
    )


def test_pass_applies_success_and_insufficient(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    storage.save_fingerprint(make_fp("aaaaaaaaaaaaaaaa", WorkState.VERIFYING))
    storage.save_fingerprint(make_fp("bbbbbbbbbbbbbbbb", WorkState.VERIFYING))

    def inputs_for(fp: Fingerprint) -> WatchInputs:
        quiet = fp.fp_hash == "aaaaaaaaaaaaaaaa"
        return WatchInputs(
            occurrences_after_fix=0 if quiet else 5,
            grace_window_elapsed=True,
            fix_deployed_to_environment=True,
        )

    results = VerificationWatcher(storage, forge, grace_inputs=inputs_for).run_once()

    by_hash = {r.fp_hash: r.verdict for r in results}
    assert by_hash["aaaaaaaaaaaaaaaa"] is Verdict.SUCCESS
    assert by_hash["bbbbbbbbbbbbbbbb"] is Verdict.INSUFFICIENT
    assert storage.get_fingerprint("aaaaaaaaaaaaaaaa").state is WorkState.VERIFIED_CLOSED
    assert storage.get_fingerprint("bbbbbbbbbbbbbbbb").state is WorkState.REOPENED
    # forge lifecycle closed (R-09): verified label + close, recurred label + reopen
    assert [ref.issue_id for ref in forge.closed] == ["console-1"]
    assert [ref.issue_id for ref in forge.reopened] == ["console-1"]
    applied = [label for _, label in forge.labels]
    assert LABEL_VERIFIED in applied and LABEL_RECURRED in applied


def test_insufficient_marks_chronic_blocking_future_auto_fixes(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp("dddddddddddddddd", WorkState.VERIFYING))
    watcher = VerificationWatcher(
        storage,
        ConsoleForge(),
        grace_inputs=lambda fp: WatchInputs(occurrences_after_fix=3, grace_window_elapsed=True),
    )
    watcher.run_once()
    stored = storage.get_fingerprint("dddddddddddddddd")
    assert stored is not None and stored.chronic is True, "repeat-fix inhibition engaged"


def test_verifying_only_non_verifying_ignored(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp("cccccccccccccccc", WorkState.OPEN))

    def inputs_for(fp: Fingerprint) -> WatchInputs:
        return WatchInputs(occurrences_after_fix=0, grace_window_elapsed=True)

    watcher = VerificationWatcher(storage, ConsoleForge(), grace_inputs=inputs_for)
    assert watcher.run_once() == []  # OPEN state is not watched


def test_grace_inputs_resume_persisted_window_and_count_from_anchor() -> None:
    started = datetime.now(UTC) - timedelta(minutes=45)
    fp = make_fp("eeeeeeeeeeeeeeee", WorkState.VERIFYING).model_copy(
        update={
            "verification_started_at": started,
            "verification_start_occurrences": 10,
            "occurrences": 13,
        }
    )
    watch = grace_inputs_from_storage(fp, lambda: datetime.now(UTC).timestamp(), grace_minutes=30)
    assert watch.grace_window_elapsed is True
    assert watch.occurrences_after_fix == 3  # 13 now − 10 at window start
