# SPDX-License-Identifier: Apache-2.0
"""Verdict decision tests (WF-08, VER-2)."""

from __future__ import annotations

from wakey.policy.verification import Verdict, WatchInputs, decide_verdict


def test_success_when_silent() -> None:
    watch = WatchInputs(
        occurrences_after_fix=0, grace_window_elapsed=True, fix_deployed_to_environment=True
    )
    assert decide_verdict(watch) is Verdict.SUCCESS


def test_insufficient_when_still_firing() -> None:
    watch = WatchInputs(
        occurrences_after_fix=7, grace_window_elapsed=True, fix_deployed_to_environment=True
    )
    assert decide_verdict(watch) is Verdict.INSUFFICIENT


def test_inconclusive_when_fix_not_deployed() -> None:
    watch = WatchInputs(
        occurrences_after_fix=0,
        grace_window_elapsed=True,
        fix_deployed_to_environment=False,
    )
    assert decide_verdict(watch) is Verdict.INCONCLUSIVE


def test_inconclusive_when_window_not_elapsed() -> None:
    watch = WatchInputs(
        occurrences_after_fix=0,
        grace_window_elapsed=False,
        fix_deployed_to_environment=True,
    )
    assert decide_verdict(watch) is Verdict.INCONCLUSIVE


def test_revert_is_inconclusive_not_failure() -> None:
    watch = WatchInputs(
        occurrences_after_fix=0,
        grace_window_elapsed=True,
        fix_deployed_to_environment=True,
        reverted_by_human=True,
    )
    assert decide_verdict(watch) is Verdict.INCONCLUSIVE
