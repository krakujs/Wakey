# SPDX-License-Identifier: Apache-2.0
"""Repro-first fix agent core (WF-06, M2).

Loop: write a failing reproduction test from the RCA evidence, then patch
until the suite is green — a proposal without a failing repro is rejected
(A1: proves the agent understood the bug). The model tier only *proposes*
content; this module owns the gates, the sandboxed test run, and the diff.

Containment (R-03): proposer-supplied paths are resolved against the
workspace and rejected on absolute paths, ``..`` traversal, and symlink
escapes; test runs execute with a minimal environment, cgroup-style
resource limits (CPU/memory/processes/filesize), a killed process group
on timeout, and network disabled via a namespace when the platform
provides it. Full isolation guarantees require the capped-container gate
(E10-T2); live autonomous fixing stays disabled until those pass.

Baseline discipline (R-08): the original workspace snapshot is immutable
for the whole loop; the final diff is generated against it (never against
the previous attempt), the repro's failure output is fed to the proposer,
and a patch may never touch the repro file itself.
"""

from __future__ import annotations

import difflib
import logging
import os
import resource
import shutil
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from wakey.core.config import Autonomy
from wakey.core.models import Fingerprint

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset(
    {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".php", ".go", ".rb", ".rs"}
)
SKIPPED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "wakey-data"})
MAX_SNAPSHOT_FILE_BYTES = 512 * 1024
MAX_OUTPUT_CHARS = 6000
DEFAULT_TIMEOUT_S = 300


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


class SandboxError(Exception):
    """A proposed path or command violated containment — hard stop (R-03)."""


def safe_resolve(workspace: Path, relative: str) -> Path:
    """Resolve ``relative`` inside ``workspace``; refuse every escape.

    Rejects absolute paths, ``..`` traversal, and — after resolution —
    any symlink whose target leaves the workspace (R-03).
    """
    candidate = Path(relative)
    if candidate.is_absolute():
        raise SandboxError(f"absolute path rejected: {relative}")
    resolved = (workspace / candidate).resolve()
    root = workspace.resolve()
    if resolved != root and root not in resolved.parents:
        raise SandboxError(f"path escapes the workspace: {relative}")
    return resolved


def snapshot_files(workspace: Path) -> dict[str, str]:
    """Supported text files under the workspace, relative-path keyed.

    Vendor/VCS/runtime directories are skipped; oversized files are left
    out (and therefore never patched) rather than read into memory.
    """
    files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.suffix not in SUPPORTED_EXTENSIONS:
            continue
        if SKIPPED_DIRS & set(path.relative_to(workspace).parts):
            continue
        if path.stat().st_size > MAX_SNAPSHOT_FILE_BYTES:
            logger.warning("snapshot skipped oversized file %s", path)
            continue
        files[str(path.relative_to(workspace))] = path.read_text(encoding="utf-8", errors="replace")
    return files


def write_proposed_files(workspace: Path, files: dict[str, str]) -> None:
    """Write proposer output through the containment gate.

    Every path is validated *before* anything is written, so a patch
    mixing valid paths with an escape attempt writes nothing at all.
    """
    targets = [(safe_resolve(workspace, relative), content) for relative, content in files.items()]
    for target, content in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _child_limits() -> None:  # pragma: no cover - exercised in subprocess
    resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_TIMEOUT_S, DEFAULT_TIMEOUT_S))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    # NPROC is per-user on Linux, not per-tree: keep it generous (the
    # container pids-limit is the real per-tree control, E10-T2)
    resource.setrlimit(resource.RLIMIT_NPROC, (512, 512))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))


_NET_NS_CACHE: list[bool] = []  # one-entry probe cache (empty = not yet probed)


def _network_ns_usable() -> bool:
    """One-time probe: can this host create network namespaces? (Linux)"""
    if not _NET_NS_CACHE:
        try:
            probe = subprocess.run(  # noqa: S603
                ["unshare", "--net", "true"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            _NET_NS_CACHE.append(probe.returncode == 0)
        except (OSError, subprocess.TimeoutExpired):
            _NET_NS_CACHE.append(False)
    return _NET_NS_CACHE[0]


def run_tests(
    workspace: Path,
    command: list[str],
    timeout_s: int = DEFAULT_TIMEOUT_S,
    network_ns: bool = True,
) -> TestOutcome:
    """Run the repo's test command resource-capped, network-off by default.

    The child runs in its own process group with a minimal environment; on
    timeout the whole group is killed. Network isolation uses ``unshare
    --net`` when the platform allows it; on unprivileged hosts the run
    proceeds without it and the limitation is logged — the capped-container
    gate (E10-T2) covers the difference before live fixing is ever enabled.
    """
    argv = list(command)
    if network_ns and shutil.which("unshare") and _network_ns_usable():
        argv = ["unshare", "--net", *argv]
    else:
        logger.warning("network namespace unavailable — test run has host network access")
    minimal_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workspace),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv from service config
            argv,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=minimal_env,
            start_new_session=True,  # own session/process group → group kill is safe
            preexec_fn=(  # noqa: PLW1509 — single-shot child, no shared state
                _child_limits if os.name == "posix" else None
            ),
        )
    except OSError as exc:
        return TestOutcome(False, f"test command could not start: {exc}")
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # pgid == pid (new session)
        except (ProcessLookupError, PermissionError):  # pragma: no cover - race
            proc.kill()
        stdout, stderr = proc.communicate()
        output = (stdout or "") + (stderr or "")
        return TestOutcome(
            False,
            f"test run exceeded {timeout_s}s — process group killed. " + output[-MAX_OUTPUT_CHARS:],
        )
    output = (stdout or "") + (stderr or "")
    return TestOutcome(proc.returncode == 0, output[-MAX_OUTPUT_CHARS:])


class Proposer(Protocol):
    """Returns updated file contents for the workspace (path → full content)."""

    def repro_test(self, fingerprint: Fingerprint, files: dict[str, str]) -> tuple[str, str]: ...

    def patch(
        self,
        fingerprint: Fingerprint,
        rca_summary: str,
        files: dict[str, str],
        last_failure: str = "",
    ) -> dict[str, str]: ...


@dataclass(frozen=True)
class FixResult:
    ok: bool
    reason: str
    diff: str = ""
    attempts: int = 0
    files: dict[str, str] = field(default_factory=dict)  # changed files, final content


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
        original = snapshot_files(workspace)  # immutable baseline (R-08)
        repro_name, repro_content = self._proposer.repro_test(fingerprint, original)
        write_proposed_files(workspace, {repro_name: repro_content})

        baseline = self._runner(workspace, test_command)
        if baseline.ok:
            return FixResult(False, "repro test passed before any patch — it does not reproduce")
        last_failure = baseline.output

        for attempt in range(1, self._max_iterations + 1):
            patch = self._proposer.patch(
                fingerprint, rca_summary, snapshot_files(workspace), last_failure=last_failure
            )
            self._validate_patch(workspace, patch, repro_name)
            write_proposed_files(workspace, patch)
            outcome = self._runner(workspace, test_command)
            if outcome.ok:
                final = snapshot_files(workspace)
                changed = {
                    path: content
                    for path, content in final.items()
                    if original.get(path) != content
                }
                return FixResult(
                    True,
                    f"repro green after {attempt} patch attempt(s)",
                    self._diff(original, final),
                    attempt,
                    changed,
                )
            last_failure = outcome.output

        return FixResult(False, f"no green patch after {self._max_iterations} iterations")

    def _validate_patch(self, workspace: Path, patch: dict[str, str], repro_name: str) -> None:
        """Containment plus repro-integrity: no escapes, no repro tampering."""
        for relative in patch:
            safe_resolve(workspace, relative)  # raises SandboxError on escape
            if str(Path(relative)) == str(Path(repro_name)):
                raise SandboxError("proposer may not modify its own reproduction test")

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
