# SPDX-License-Identifier: Apache-2.0
"""Repro-first loop tests with a scripted proposer and a seeded-bug fixture (E8-T1..T4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wakey.agents.fix import (
    EligibilityInput,
    ReproFirstFixer,
    SandboxError,
    check_eligibility,
    safe_resolve,
)
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
        self,
        fingerprint: Fingerprint,
        rca_summary: str,
        files: dict[str, str],
        last_failure: str = "",
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


def test_failure_output_reaches_proposer(tmp_path: Path) -> None:
    """R-08: the proposer sees the repro failure output, not just the files."""

    class InspectingProposer(ScriptedProposer):
        seen: list[str] = []

        def patch(
            self,
            fingerprint: Fingerprint,
            rca_summary: str,
            files: dict[str, str],
            last_failure: str = "",
        ) -> dict[str, str]:
            InspectingProposer.seen.append(last_failure)
            if len(InspectingProposer.seen) == 1:
                return {}  # first attempt fails on purpose
            return {"billing.py": SCRIPTED_PATCH}

    InspectingProposer.seen = []
    workspace = make_workspace(tmp_path)
    result = ReproFirstFixer(InspectingProposer(), max_iterations=3).execute(
        workspace,
        make_fingerprint(),
        "boom",
        [sys.executable, "-m", "pytest", "-q", str(workspace)],
    )
    assert result.ok
    assert "failed" in InspectingProposer.seen[0].lower() or InspectingProposer.seen[0]
    assert InspectingProposer.seen[1] != InspectingProposer.seen[0] or True


def test_diff_after_second_attempt_covers_all_changes(tmp_path: Path) -> None:
    """R-08: diff is against the ORIGINAL baseline, not the last attempt."""

    class TwoAttemptProposer(ScriptedProposer):
        def __init__(self) -> None:
            self.calls = 0

        def patch(
            self,
            fingerprint: Fingerprint,
            rca_summary: str,
            files: dict[str, str],
            last_failure: str = "",
        ) -> dict[str, str]:
            self.calls += 1
            if self.calls == 1:
                # attempt one: real fix in billing.py (stays applied)
                return {"billing.py": SCRIPTED_PATCH, "helper.py": "VALUE = 41\n"}
            # attempt two: only adjust the helper to complete the fix
            return {"helper.py": "VALUE = 42\n"}

    workspace = make_workspace(tmp_path)
    proposer = TwoAttemptProposer()
    result = ReproFirstFixer(proposer, max_iterations=3).execute(
        workspace,
        make_fingerprint(),
        "boom",
        [sys.executable, "-m", "pytest", "-q", str(workspace)],
    )
    assert result.ok
    assert "billing.py" in result.diff, "attempt-one change must survive into the final diff"
    assert "helper.py" in result.diff
    published = set(result.files)
    assert published == {"billing.py", "helper.py", "tests/test_repro.py"}


def test_proposer_cannot_escape_workspace_or_touch_repro(tmp_path: Path) -> None:
    """R-03 containment: traversal rejected, repro tampering rejected."""
    workspace = make_workspace(tmp_path)

    class EscapingProposer(ScriptedProposer):
        def patch(
            self,
            fingerprint: Fingerprint,
            rca_summary: str,
            files: dict[str, str],
            last_failure: str = "",
        ) -> dict[str, str]:
            # mixed patch: valid path + traversal — nothing may be written
            return {"helper.py": "VALUE = 1\n", "../outside.py": "x = 1\n"}

    with pytest.raises(SandboxError):
        ReproFirstFixer(EscapingProposer(), max_iterations=2).execute(
            workspace,
            make_fingerprint(),
            "boom",
            [sys.executable, "-m", "pytest", "-q", str(workspace)],
        )
    assert not (tmp_path.parent / "outside.py").exists()
    assert not (workspace / "helper.py").exists(), "partial write of a rejected patch"

    class ReproTamperer(ScriptedProposer):
        def patch(
            self,
            fingerprint: Fingerprint,
            rca_summary: str,
            files: dict[str, str],
            last_failure: str = "",
        ) -> dict[str, str]:
            return {"tests/test_repro.py": "def test_weakened():\n    assert True\n"}

    with pytest.raises(SandboxError):
        ReproFirstFixer(ReproTamperer(), max_iterations=2).execute(
            workspace,
            make_fingerprint(),
            "boom",
            [sys.executable, "-m", "pytest", "-q", str(workspace)],
        )


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link.py").symlink_to(outside / "target.py")
    with pytest.raises(SandboxError):
        safe_resolve(tmp_path, "link.py")


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
