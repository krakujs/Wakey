# SPDX-License-Identifier: Apache-2.0
"""Watcher pass tests (E9-T1)."""

from __future__ import annotations

from pathlib import Path

from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.policy.verification import Verdict, WatchInputs
from wakey.policy.watcher import VerificationWatcher


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
    storage.save_fingerprint(make_fp("aaaaaaaaaaaaaaaa", WorkState.VERIFYING))
    storage.save_fingerprint(make_fp("bbbbbbbbbbbbbbbb", WorkState.VERIFYING))

    def inputs_for(fp: Fingerprint) -> WatchInputs:
        quiet = fp.fp_hash == "aaaaaaaaaaaaaaaa"
        return WatchInputs(
            occurrences_after_fix=0 if quiet else 5,
            grace_window_elapsed=True,
            fix_deployed_to_environment=True,
        )

    watcher = VerificationWatcher(storage, forge=None, grace_inputs=inputs_for)
    results = watcher.run_once()

    by_hash = {r.fp_hash: r.verdict for r in results}
    assert by_hash["aaaaaaaaaaaaaaaa"] is Verdict.SUCCESS
    assert by_hash["bbbbbbbbbbbbbbbb"] is Verdict.INSUFFICIENT
    assert storage.get_fingerprint("aaaaaaaaaaaaaaaa").state is WorkState.VERIFIED_CLOSED
    assert storage.get_fingerprint("bbbbbbbbbbbbbbbb").state is WorkState.REOPENED


def test_verifying_only_non_verifying_ignored(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp("cccccccccccccccc", WorkState.OPEN))

    def inputs_for(fp: Fingerprint) -> WatchInputs:
        return WatchInputs(occurrences_after_fix=0, grace_window_elapsed=True)

    watcher = VerificationWatcher(storage, forge=None, grace_inputs=inputs_for)
    assert watcher.run_once() == []  # OPEN state is not watched
