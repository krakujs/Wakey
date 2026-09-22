# SPDX-License-Identifier: Apache-2.0
"""Exercise installer control flow without touching Docker or existing deployments."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("failure", ["", "compose version", "info", "compose up"])
def test_startup_preserves_data_and_reports_failures(tmp_path, failure):
    fake = tmp_path / "docker"
    fake.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in "$FAILURE"*) [ -z "$FAILURE" ] || exit 1;; esac\n'
        'case "$*" in *setup-token*) echo WAKE-SYNTHETIC;; esac\n'
    )
    fake.chmod(0o700)
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "FAILURE": failure,
    }
    result = subprocess.run(
        check=False,
        args=["sh", str(ROOT / "start.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (1 if failure else 0)
    invoked = calls.read_text()
    assert "down" not in invoked and "volume" not in invoked
    if failure:
        assert "setup-token" not in invoked
        assert result.stderr
    else:
        assert "up --build --wait --wait-timeout 180 wakey" in invoked
        assert "exec -T wakey wakey setup-token --force" in invoked
        assert "local demo" in result.stdout
    assert not (tmp_path / ".env").exists()


def test_startup_help_and_invalid_options():
    for option, code in [("--help", 0), ("--unknown", 2)]:
        result = subprocess.run(
            check=False,
            args=["sh", str(ROOT / "start.sh"), option],
            timeout=10,
            capture_output=True,
            text=True,
        )
        assert result.returncode == code


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI not installed")
def test_operator_compose_configuration_is_valid(tmp_path):
    # config --quiet validates locally; never starts containers or prints resolved secrets.
    secret = tmp_path / "synthetic-secret"
    secret.write_text("synthetic-only")
    env = {
        **os.environ,
        "WAKEY_CONNECT_CLIENT_ID": "synthetic-client",
        "WAKEY_CONNECT_APP_SLUG": "synthetic-wakey",
    }
    for name in ("MASTER_KEY", "PRIVATE_KEY", "WEBHOOK_SECRET"):
        env["WAKEY_CONNECT_" + name + "_FILE"] = str(secret)
    result = subprocess.run(
        check=False,
        args=[
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "deploy/compose.connect.yml"),
            "config",
            "--quiet",
        ],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0


def test_build_context_excludes_credentials_and_runtime_data():
    patterns = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in (".env", ".env.*", "**/*.pem", "**/*.key", "**/*.db", ".venv", "secrets"):
        assert pattern in patterns
