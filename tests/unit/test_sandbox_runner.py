# SPDX-License-Identifier: Apache-2.0
"""Runner policy and bounded process supervision without requiring Docker."""

import os
import subprocess
import sys

import pytest

from wakey.agents.sandbox import DockerTestRunner, SandboxError


def test_container_policy(tmp_path):
    command = DockerTestRunner("trusted@sha256:123").command(
        tmp_path, ["python", "-m", "pytest"], "job"
    )
    for flag, value in [
        ("--network", "none"),
        ("--pull", "never"),
        ("--memory", "512m"),
        ("--user", "65534:65534"),
        ("--cap-drop", "ALL"),
    ]:
        assert command[command.index(flag) + 1] == value
    assert "--read-only" in command
    assert command[command.index("--mount") + 1].endswith(",dst=/source,readonly")
    assert command[-3:] == ["python", "-m", "pytest"]


@pytest.mark.parametrize(
    "image,argv,error",
    [("", ["true"], "IMAGE"), ("-evil", ["true"], "IMAGE"), ("trusted", [], "empty")],
)
def test_invalid_configuration(tmp_path, monkeypatch, image, argv, error):
    monkeypatch.delenv("WAKEY_SANDBOX_IMAGE", raising=False)
    with pytest.raises(SandboxError, match=error):
        DockerTestRunner(image).command(tmp_path, argv, "job")


def test_missing_docker_is_not_a_failed_repro(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(SandboxError, match="host execution is forbidden"):
        DockerTestRunner("trusted")(tmp_path, ["true"])


def test_mount_delimiter_rejected(tmp_path):
    path = tmp_path / "bad,path"
    path.mkdir()
    with pytest.raises(SandboxError, match="comma"):
        DockerTestRunner("trusted").command(path, ["true"], "job")


def test_remote_daemon_mode_streams_archive_instead_of_a_bind_mount(tmp_path):
    runner = DockerTestRunner("trusted", workspace_mode="copy")
    command = runner._container_command(tmp_path, ["pytest", "-q"], "job", stream_workspace=True)
    assert "--interactive" in command
    assert "--mount" not in command
    assert any("tar -xz -C /workspace" in part for part in command)


@pytest.mark.parametrize(
    "script,expected",
    [
        ("print('x'*20000)", True),
        ("raise SystemExit(1)", False),
        ("raise SystemExit(125)", "start"),
        ("import time; time.sleep(5)", "timeout"),
    ],
)
def test_supervision_and_cleanup(tmp_path, monkeypatch, script, expected):
    runner = DockerTestRunner("trusted", timeout_s=0.2)
    monkeypatch.setattr("shutil.which", lambda _: "/synthetic/docker")
    # Exercise the supervisor against a harmless local process, not a repo command.
    monkeypatch.setattr(runner, "command", lambda *_: [sys.executable, "-c", script])
    cleaned = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: cleaned.append(argv))
    if isinstance(expected, str):
        with pytest.raises(SandboxError, match=expected):
            runner(tmp_path, ["ignored"])
    else:
        result = runner(tmp_path, ["ignored"])
        assert result.ok is expected
        assert len(result.output) <= 6000
    assert len(cleaned) == 1
    assert cleaned[0][:3] == ["docker", "rm", "--force"]
    assert cleaned[0][-1].startswith("wakey-test-")


@pytest.mark.skipif(os.environ.get("WAKEY_DOCKER_TESTS") != "1", reason="opt-in Docker gate")
def test_real_container_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("WAKEY_TEST_SECRET", "must-not-reach-container")
    tmp_path.chmod(0o755)
    (tmp_path / "fixture.txt").write_text("original")
    script = """
import os, pathlib, socket
assert os.getuid() != 0
assert 'WAKEY_TEST_SECRET' not in os.environ
assert not pathlib.Path('/var/run/docker.sock').exists()
for target in ['/source/fixture.txt', '/etc/wakey-test']:
    try:
        pathlib.Path(target).write_text('forbidden')
    except OSError:
        pass
    else:
        raise AssertionError('writable protected path')
pathlib.Path('fixture.txt').write_text('sandbox-only')
try:
    socket.create_connection(('192.0.2.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('unexpected network access')
print('ISOLATED')
"""
    result = DockerTestRunner("wakey:sandbox", timeout_s=15)(tmp_path, ["python", "-c", script])
    assert result.ok, result.output
    assert "ISOLATED" in result.output
    assert (tmp_path / "fixture.txt").read_text() == "original"


@pytest.mark.skipif(os.environ.get("WAKEY_DOCKER_TESTS") != "1", reason="opt-in Docker gate")
def test_real_remote_daemon_workspace_copy(tmp_path):
    (tmp_path / "fixture.txt").write_text("copied")
    result = DockerTestRunner("wakey:sandbox", timeout_s=15, workspace_mode="copy")(
        tmp_path, ["python", "-c", "print(open('fixture.txt').read())"]
    )
    assert result.ok, result.output
    assert "copied" in result.output
