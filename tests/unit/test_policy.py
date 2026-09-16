# SPDX-License-Identifier: Apache-2.0
"""Tests: the wake-up decision (E5-T5)."""

from __future__ import annotations

from wakey.core.config import Autonomy, ServiceYaml
from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.policy.gate import Action, decide

CONFIG = ServiceYaml(immediate_count=3)


def make_fp(**overrides: object) -> Fingerprint:
    defaults: dict[str, object] = {
        "fp_hash": "3f9a1b2c4d5e6f70",
        "service": "payments-api",
        "environment": "prod",
        "template": "boom",
        "severity": Severity.ERROR,
        "occurrences": 1,
    }
    defaults.update(overrides)
    return Fingerprint(**defaults)  # type: ignore[arg-type]


def test_below_threshold_records() -> None:
    assert decide(make_fp(occurrences=2), CONFIG).action is Action.RECORD


def test_threshold_reached_wakes() -> None:
    decision = decide(make_fp(occurrences=3), CONFIG)
    assert decision.action is Action.WAKE
    assert "3 occurrences" in decision.reason


def test_critical_wakes_immediately() -> None:
    assert decide(make_fp(severity=Severity.CRITICAL, occurrences=1), CONFIG).action is Action.WAKE


def test_busy_state_absorbs() -> None:
    decision = decide(make_fp(occurrences=50, state=WorkState.FIXING), CONFIG)
    assert decision.action is Action.ABSORBED
    assert decision.reason.startswith("work already in flight")


def test_human_decisions_stay_quiet() -> None:
    assert decide(make_fp(state=WorkState.DROPPED), CONFIG).action is Action.SUPPRESSED
    assert decide(make_fp(state=WorkState.CLOSED_HUMAN), CONFIG).action is Action.SUPPRESSED


def test_observe_mode_never_wakes() -> None:
    loud = make_fp(occurrences=999, severity=Severity.CRITICAL)
    config = ServiceYaml(autonomy=Autonomy.OBSERVE)
    assert decide(loud, config).action is Action.RECORD
