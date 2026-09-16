# SPDX-License-Identifier: Apache-2.0
"""Repro-first loop tests with a scripted proposer and a seeded-bug fixture (E8-T1..T4)."""

from __future__ import annotations

import sys
from pathlib import Path

from wakey.agents.fix import EligibilityInput, ReproFirstFixer, check_eligibility
from wakey.core.config import Autonomy
from wakey.core.models import Fingerprint, Severity

SEEDED_BUG = 'def get_idempotency_key(order):\n    return order["idempotency_key"]\n'
SCRIPTED_PATCH = 'def get_idempotency_key(order):\n    return order.get("idempotency_key")\n'
REPRO = (
    "from billing import get_idempotency_key\n"
    "\n"
    "\n"
    "def test_missing_key_returns_none():\n"
    "    assert get_idempotency_key({}) is None\n"
)


class ScriptedProposer:
    """Stands in for the model: knows the seeded bug's repro and fix."""

    def repro_test(self, fingerprint: Fingerprint, files: dict[str, str]) -> tuple[str, str]:
        return "tests/test_repro.py", REPRO

    def patch(
        self, fingerprint: Fingerprint, rca_summary: str, files: dict[str, str]
    ) -> dict[str, str]:
        return {"billing.py": SCRIPTED_PATCH}


def make_workspace(tmp_path: Path) -> Path:
    (tmp_path / "billing.py").write_text(SEEDED_BUG)
    (tmp_path / "tests_existing").mkdir()
    return tmp_path


def make_fingerprint() -> Fingerprint:
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="KeyError: 'idempotency_key'",
        severity=Severity.ERROR,
        occurrences=5,
    )


def test_eligibility_gate_refusal_reasons() -> None:
    refusals = [
        EligibilityInput("needs-human", 0.9, Autonomy.FIX, 0.75, 0, 3),
        EligibilityInput("code-fix", 0.5, Autonomy.FIX, 0.75, 0, 3),
        EligibilityInput("code-fix", 0.9, Autonomy.TRIAGE, 0.75, 0, 3),
        EligibilityInput("code-fix", 0.9, Autonomy.FIX, 0.75, 3, 3),
        EligibilityInput("code-fix", 0.9, Autonomy.FIX, 0.75, 0, 3, chronic=True),
    ]
    for inputs in refusals:
        assert not check_eligibility(inputs).allowed

    ok = EligibilityInput("code-fix", 0.9, Autonomy.FIX, 0.75, 0, 3)
    assert check_eligibility(ok).allowed


def test_repro_first_loop_end_to_end(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    fixer = ReproFirstFixer(ScriptedProposer(), max_iterations=3)
    result = fixer.execute(
        workspace,
        make_fingerprint(),
        "KeyError for legacy orders missing idempotency key",
        [sys.executable, "-m", "pytest", "-q", str(workspace)],
    )
    assert result.ok, result.reason
    assert "tests/test_repro.py" in result.diff
    assert "billing.py" in result.diff
    assert (workspace / "billing.py").read_text() == SCRIPTED_PATCH


def test_non_reproducible_bug_aborts(tmp_path: Path) -> None:
    class NeverFailsProposer(ScriptedProposer):
        def repro_test(self, fingerprint: Fingerprint, files: dict[str, str]) -> tuple[str, str]:
            return "tests/test_repro.py", "def test_trivially_true():\n    assert True\n"

    workspace = make_workspace(tmp_path)
    result = ReproFirstFixer(NeverFailsProposer()).execute(
        workspace,
        make_fingerprint(),
        "nothing is wrong",
        [sys.executable, "-m", "pytest", "-q", str(workspace)],
    )
    assert not result.ok
    assert "does not reproduce" in result.reason
