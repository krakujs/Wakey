# SPDX-License-Identifier: Apache-2.0
"""Fix-executor sandbox gate (E10-T2): negative tests for the capped container.

Run via ``make gate-sandbox``, which executes this suite inside a
disposable container with ``--network none``, ``--memory 512m``,
``--cpus 1`` and ``--pids-limit 64``. Tests marked with
``requires_container_isolation`` assert the *container's* controls and
skip on a plain host run (``make check`` stays host-safe); the rest are
the host-side containment tests re-run for depth.

This gate is the barrier that must pass before live autonomous fixing is
ever enabled (review R-03; SKILL never-rule).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from wakey.agents.fix import SandboxError, run_tests, safe_resolve

CONTAINER_GATE = os.environ.get("SANDBOX_GATE") == "1"

requires_container_isolation = pytest.mark.skipif(
    not CONTAINER_GATE,
    reason="asserts container-level controls; run via `make gate-sandbox`",
)


def test_workspace_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "link.txt").symlink_to(outside)

    with pytest.raises(SandboxError):
        safe_resolve(workspace, "link.txt")
    with pytest.raises(SandboxError):
        safe_resolve(workspace, "/etc/passwd")
    with pytest.raises(SandboxError):
        safe_resolve(workspace, "../../etc/passwd")


def test_timeout_kills_the_child(tmp_path: Path) -> None:
    """A test run that outlives its timeout must be killed, promptly."""
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import os, time\nprint('pid', os.getpid(), flush=True)\ntime.sleep(600)\n")
    start = time.monotonic()
    outcome = run_tests(tmp_path, [sys.executable, str(sleeper)], timeout_s=3)
    elapsed = time.monotonic() - start
    assert not outcome.ok and "killed" in outcome.output
    assert elapsed < 15, "process-group kill must be prompt"
    child_pid = int(outcome.output.split("pid ")[1].split()[0])
    time.sleep(0.5)  # give the OS a moment to reap
    assert not Path(f"/proc/{child_pid}").exists(), (
        "the timed-out test process must have been killed"
    )


@requires_container_isolation
def test_grandchildren_die_with_the_group(tmp_path: Path) -> None:
    """A test run that spawns its own children must not leave them behind."""
    pid_file = tmp_path / "child.pid"
    spawner = tmp_path / "spawner.py"
    spawner.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(600)\n"
    )
    outcome = run_tests(tmp_path, [sys.executable, str(spawner)], timeout_s=3)
    assert not outcome.ok and "killed" in outcome.output
    time.sleep(0.5)
    child_pid = int(pid_file.read_text().strip())
    cmdline_path = Path(f"/proc/{child_pid}/cmdline")
    assert not cmdline_path.exists() or "sleep" not in cmdline_path.read_text(
        encoding="utf-8", errors="replace"
    ), (
        "the spawned grandchild must have been killed with the process group "
        "(pid reuse toleranted by cmdline check)"
    )


def test_minimal_environment_hides_operator_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with a secret in OUR environment, the child's env must not have it."""
    monkeypatch.setenv("SECRET_CANARY_XYZ", "super-secret-value")
    prober = tmp_path / "prober.py"
    prober.write_text(
        "import json, sys\n"
        "print(json.dumps({'leak': [k for k in sys.argv[1:] if k in __import__('os').environ]}))\n"
    )
    probe_names = ["WAKEY_GITHUB_TOKEN", "WAKEY_LLM_API_KEY", "SECRET_CANARY_XYZ"]
    outcome = run_tests(tmp_path, [sys.executable, str(prober), *probe_names])
    assert outcome.ok
    assert "super-secret-value" not in outcome.output
    assert "SECRET_CANARY_XYZ" not in outcome.output


def test_output_is_bounded(tmp_path: Path) -> None:
    chatty = tmp_path / "chatty.py"
    chatty.write_text("print('x' * 5_000_000)\n")
    outcome = run_tests(tmp_path, [sys.executable, str(chatty)], timeout_s=30)
    assert len(outcome.output) <= 7000, "output capture must be bounded"


@requires_container_isolation
def test_network_egress_blocked_by_container(tmp_path: Path) -> None:
    """Inside `--network none`, any connect attempt must fail immediately."""
    prober = tmp_path / "net_probe.py"
    prober.write_text(
        "import socket, sys\n"
        "s = socket.socket()\n"
        "s.settimeout(3)\n"
        "try:\n"
        "    s.connect(('93.184.216.34', 80))\n"  # example.com — must be unreachable
        "    sys.exit(7)\n"
        "except OSError:\n"
        "    sys.exit(0)\n"
    )
    outcome = run_tests(tmp_path, [sys.executable, str(prober)], network_ns=False)
    assert outcome.ok, "network egress succeeded inside the no-network container"


@requires_container_isolation
def test_memory_cap_enforced_by_container(tmp_path: Path) -> None:
    """Inside the 512MB container, a 1GB allocator must die, not thrive."""
    hog = tmp_path / "hog.py"
    hog.write_text("data = bytearray(1024 * 1024 * 1024)\nprint('allocated')\n")
    outcome = run_tests(tmp_path, [sys.executable, str(hog)], timeout_s=60)
    assert not outcome.ok
    assert "allocated" not in outcome.output


@requires_container_isolation
def test_pids_cap_enforced_by_container(tmp_path: Path) -> None:
    """Inside `--pids-limit 64`, fork-storming must fail rather than succeed."""
    forker = tmp_path / "forker.py"
    forker.write_text(
        "import subprocess, sys, time\n"
        "children = []\n"
        "for _ in range(300):\n"
        "    try:\n"
        "        children.append(subprocess.Popen([sys.executable, '-c',\n"
        "            'import time; time.sleep(60)']))\n"
        "    except OSError:\n"
        "        break\n"
        "print(f'made={len(children)}', flush=True)\n"
        "time.sleep(20)  # keep holding pids so the limit is provable\n"
    )
    outcome = run_tests(tmp_path, [sys.executable, str(forker)], timeout_s=45)
    assert not outcome.ok or "made=300" not in outcome.output, (
        "a capped container must eventually refuse new concurrent processes"
    )
