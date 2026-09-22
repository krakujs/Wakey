# SPDX-License-Identifier: Apache-2.0
"""Isolated fix-executor service and remote runner (readiness #5).

The executor is the production topology's only container-runtime host.
These tests pin its narrow-authority contract: bearer auth, bounded
uploads, safe extraction, concurrency limits — and the client's ability
to run tests through it without any local Docker.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from wakey.agents.executor_server import create_executor_app
from wakey.agents.fix import TestOutcome
from wakey.agents.sandbox import RemoteTestRunner, SandboxError, pack_workspace
from wakey.core.config import ConfigError, Settings

TOKEN = "executor-shared-token"


def _tarball(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeRunner:
    """Stands in for DockerTestRunner; records what the executor invoked."""

    def __init__(self, outcome: TestOutcome | None = None) -> None:
        self.calls: list[tuple[list[str], list[str]]] = []
        self.outcome = outcome or TestOutcome(True, "1 passed")

    def __call__(self, workspace: Path, argv: list[str]) -> TestOutcome:
        self.calls.append((sorted(p.name for p in workspace.rglob("*")), list(argv)))
        return self.outcome


def _client(runner: FakeRunner, **kw: object) -> TestClient:
    app = create_executor_app(runner, TOKEN, **kw)  # type: ignore[arg-type]
    return TestClient(app)


def _run(client: TestClient, files: dict[str, str], argv: list[str], auth: bool = True):
    return client.post(
        "/v1/run",
        files={"workspace": ("workspace.tar.gz", _tarball(files), "application/gzip")},
        data={"argv": json.dumps(argv)},
        headers={"Authorization": f"Bearer {TOKEN}"} if auth else {},
    )


def test_healthz_reports_ready() -> None:
    response = _client(FakeRunner()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "running": 0}


def test_run_requires_bearer_token() -> None:
    response = _run(_client(FakeRunner()), {"test_x.py": "def t(): pass\n"}, ["pytest"], auth=False)
    assert response.status_code == 403


def test_run_extracts_workspace_and_returns_outcome() -> None:
    runner = FakeRunner()
    response = _run(_client(runner), {"test_x.py": "def t(): pass\n"}, ["pytest", "-q"])
    assert response.status_code == 200
    assert response.json() == {"ok": True, "output": "1 passed"}
    assert runner.calls[0][0] == ["test_x.py"]
    assert runner.calls[0][1] == ["pytest", "-q"]


def test_run_rejects_traversal_tarball() -> None:
    malicious = io.BytesIO()
    with tarfile.open(fileobj=malicious, mode="w:gz") as archive:
        data = b"evil"
        info = tarfile.TarInfo("../escape.py")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    client = _client(FakeRunner())
    response = client.post(
        "/v1/run",
        files={"workspace": ("w.tar.gz", malicious.getvalue(), "application/gzip")},
        data={"argv": json.dumps(["pytest"])},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 422


def test_run_rejects_bad_argv() -> None:
    client = _client(FakeRunner())
    response = client.post(
        "/v1/run",
        files={"workspace": ("w.tar.gz", _tarball({"a.py": "x"}), "application/gzip")},
        data={"argv": "not-a-list"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 422


def test_run_reports_sandbox_infrastructure_failure_as_503() -> None:
    class BrokenRunner(FakeRunner):
        def __call__(self, workspace: Path, argv: list[str]) -> TestOutcome:
            raise SandboxError("WAKEY_SANDBOX_IMAGE must name a pre-provisioned test image")

    response = _run(_client(BrokenRunner()), {"a.py": "x"}, ["pytest"])
    assert response.status_code == 503


def test_executor_never_builds_without_a_token() -> None:
    with pytest.raises(SystemExit):
        create_executor_app(FakeRunner(), "")


def _remote_runner(handler: httpx.Handler, tmp_path: Path) -> tuple[RemoteTestRunner, Path]:
    runner = RemoteTestRunner(
        "http://executor:8490",
        TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")
    return runner, tmp_path


def test_remote_runner_round_trip(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert b'name="workspace"' in request.read()  # tarball is gzipped opaque
        return httpx.Response(200, json={"ok": True, "output": "1 passed"})

    runner, workspace = _remote_runner(handler, tmp_path)
    outcome = runner(workspace, ["pytest", "-q"])
    assert outcome.ok and "passed" in outcome.output


def test_remote_runner_maps_unreachable_executor_to_sandbox_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    runner, workspace = _remote_runner(handler, tmp_path)
    with pytest.raises(SandboxError, match="unreachable"):
        runner(workspace, ["pytest"])


def test_remote_runner_maps_http_rejection_to_sandbox_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    runner, workspace = _remote_runner(handler, tmp_path)
    with pytest.raises(SandboxError, match="503"):
        runner(workspace, ["pytest"])


def test_pack_workspace_skips_symlinks_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("x = 1\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "sub" / "a.py")
    raw = pack_workspace(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        names = archive.getnames()
    assert "sub/a.py" in names
    assert "link.py" not in names


def test_settings_require_token_with_executor_url() -> None:
    with pytest.raises(ConfigError, match="EXECUTOR_TOKEN"):
        Settings.from_env({"WAKEY_SANDBOX_EXECUTOR_URL": "http://fixer:8490"})
    ok = Settings.from_env(
        {
            "WAKEY_SANDBOX_EXECUTOR_URL": "http://fixer:8490",
            "WAKEY_SANDBOX_EXECUTOR_TOKEN": "tok",
        }
    )
    assert ok.sandbox_executor_token == "tok"
