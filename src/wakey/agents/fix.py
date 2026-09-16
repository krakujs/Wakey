# SPDX-License-Identifier: Apache-2.0
"""Repro-first fix agent core (WF-06, M2).

Loop: write a failing reproduction test from the RCA evidence, then patch
until the suite is green — a proposal without a failing repro is rejected
(A1: proves the agent understood the bug). The model tier only *proposes*
content; this module owns the gates, the sandboxed test run, and the diff.

The proposer stands in for the LLM: tests use a scripted one; the live tier
implements the same protocol against a model (E8-T5+).
"""

from __future__ import annotations

import difflib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from wakey.core.config import Autonomy
from wakey.core.models import Fingerprint


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class EligibilityInput:
    """Everything the FIX-1 gate weighs."""

    rca_class: str
    confidence: float
    autonomy: Autonomy
    confidence_floor: float
    open_prs: int
    max_open_prs: int
    chronic: bool = False


def check_eligibility(inputs: EligibilityInput) -> Eligibility:
    """FIX-1 gate: every refusal names the exact failed condition."""
    if inputs.rca_class != "code-fix":
        return Eligibility(False, f"RCA class is {inputs.rca_class}, not code-fix")
    if inputs.confidence < inputs.confidence_floor:
        return Eligibility(
            False, f"confidence {inputs.confidence:.2f} < floor {inputs.confidence_floor:.2f}"
        )
    if inputs.autonomy is not Autonomy.FIX:
        return Eligibility(
            False, f"autonomy is {inputs.autonomy.value}, fix requires 'fix' or @wakey fix"
        )
    if inputs.chronic:
        return Eligibility(False, "fingerprint is chronic — human ack required (VER-4)")
    if inputs.open_prs >= inputs.max_open_prs:
        return Eligibility(
            False, f"open wakey PRs ({inputs.open_prs}) reached cap {inputs.max_open_prs}"
        )
    return Eligibility(True, "eligible")


@dataclass(frozen=True)
class TestOutcome:
    ok: bool
    output: str


def run_tests(workspace: Path, command: list[str], timeout_s: int = 300) -> TestOutcome:
    """Run the repo's own test command in the workspace, capped by timeout."""
    try:
        proc = subprocess.run(  # noqa: S603, PLW1510 — returncode drives the verdict
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return TestOutcome(proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:])
    except subprocess.TimeoutExpired:
        return TestOutcome(False, f"test run exceeded {timeout_s}s — aborted")


class Proposer(Protocol):
    """Returns updated file contents for the workspace (path → full content)."""

    def repro_test(self, fingerprint: Fingerprint, files: dict[str, str]) -> tuple[str, str]: ...

    def patch(
        self, fingerprint: Fingerprint, rca_summary: str, files: dict[str, str]
    ) -> dict[str, str]: ...


@dataclass(frozen=True)
class FixResult:
    ok: bool
    reason: str
    diff: str = ""
    attempts: int = 0


class ReproFirstFixer:
    """Owns the loop; the proposer only suggests content."""

    def __init__(
        self,
        proposer: Proposer,
        runner: Callable[[Path, list[str]], TestOutcome] = run_tests,
        max_iterations: int = 3,
    ) -> None:
        self._proposer = proposer
        self._runner = runner
        self._max_iterations = max_iterations

    def execute(
        self,
        workspace: Path,
        fingerprint: Fingerprint,
        rca_summary: str,
        test_command: list[str],
    ) -> FixResult:
        files = {
            str(path.relative_to(workspace)): path.read_text(encoding="utf-8")
            for path in sorted(workspace.rglob("*.py"))
        }
        repro_name, repro_content = self._proposer.repro_test(fingerprint, files)
        (workspace / repro_name).parent.mkdir(parents=True, exist_ok=True)
        (workspace / repro_name).write_text(repro_content, encoding="utf-8")

        baseline = self._runner(workspace, test_command)
        if baseline.ok:
            return FixResult(False, "repro test passed before any patch — it does not reproduce")

        for attempt in range(1, self._max_iterations + 1):
            updated = self._proposer.patch(fingerprint, rca_summary, files)
            for rel_path, content in updated.items():
                (workspace / rel_path).write_text(content, encoding="utf-8")
            outcome = self._runner(workspace, test_command)
            if outcome.ok:
                final = {
                    str(path.relative_to(workspace)): path.read_text(encoding="utf-8")
                    for path in sorted(workspace.rglob("*.py"))
                }
                return FixResult(
                    True,
                    f"repro green after {attempt} patch attempt(s)",
                    self._diff(files, final),
                    attempt,
                )
            files = updated

        return FixResult(False, f"no green patch after {self._max_iterations} iterations")

    @staticmethod
    def _diff(before: dict[str, str], after: dict[str, str]) -> str:
        chunks: list[str] = []
        for path in sorted(set(before) | set(after)):
            diff = difflib.unified_diff(
                before.get(path, "").splitlines(),
                after.get(path, "").splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
            chunks.extend(diff)
        return "\n".join(chunks)
