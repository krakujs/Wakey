# SPDX-License-Identifier: Apache-2.0
"""Workspace runtime tests (E7-T1 slice): safe extraction, bootstrap, errors."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

import wakey.agents.fix as fix_mod
import wakey.agents.workspace as ws_mod
from wakey.agents.workspace import (
    MAX_TARBALL_BYTES,
    WorkspaceError,
    WorkspaceRuntime,
    extract_tarball_bytes,
)


def make_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extracts_stripping_the_github_root_prefix(tmp_path: Path) -> None:
    raw = make_tar({"owner-repo-abcd/src/app.py": b"print('hi')\n"})
    extract_tarball_bytes(raw, tmp_path)
    assert (tmp_path / "src" / "app.py").read_text() == "print('hi')\n"
    assert not (tmp_path / "owner-repo-abcd").exists()


def test_traversal_member_rejected(tmp_path: Path) -> None:
    raw = make_tar({"owner-repo/../../../etc/evil.py": b"x\n"})
    with pytest.raises(WorkspaceError, match="escapes"):
        extract_tarball_bytes(raw, tmp_path)
    assert not (tmp_path.parent / "evil.py").exists()


def test_symlink_member_rejected(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        info = tarfile.TarInfo("owner-repo/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    with pytest.raises(WorkspaceError, match="link"):
        extract_tarball_bytes(buf.getvalue(), tmp_path)


def test_oversize_tarball_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="size cap"):
        extract_tarball_bytes(b"x" * (MAX_TARBALL_BYTES + 1), tmp_path)


def test_runtime_prepare_fetches_and_extracts(tmp_path, monkeypatch) -> None:
    """prepare(): repo info + tarball fetch via the API, extracted to work root."""
    raw = make_tar({"wakey-fixture-abc123/billing.py": b"value = 1\n"})

    def fake_get(url, **kwargs):
        if str(url).endswith("/repos/acme/fixture"):
            return FakeResponse(200, json.dumps({"default_branch": "main"}).encode())
        return FakeResponse(200, raw)

    monkeypatch.setattr("wakey.agents.workspace.httpx.get", fake_get)
    runtime = WorkspaceRuntime(token="t", api_base="https://api.test", work_root=tmp_path)
    workspace = runtime.prepare("acme/fixture")
    assert (workspace.path / "billing.py").read_text() == "value = 1\n"
    assert workspace.default_branch == "main"


def test_runtime_prepare_unreachable_repo(tmp_path, monkeypatch) -> None:
    def fake_get(url, **kwargs):
        return FakeResponse(404, b"{}")

    monkeypatch.setattr("wakey.agents.workspace.httpx.get", fake_get)
    runtime = WorkspaceRuntime(token="t", api_base="https://api.test", work_root=tmp_path)
    with pytest.raises(WorkspaceError, match="unreachable"):
        runtime.prepare("acme/missing")


class FakeResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self._content = content

    @property
    def content(self) -> bytes:
        return self._content

    def json(self) -> dict:
        return json.loads(self._content)


def test_runtime_tarball_fetch_failure(tmp_path, monkeypatch) -> None:
    def fake_get(url, **kwargs):
        status = 404 if "/tarball/" in str(url) else 200
        return FakeResponse(status, json.dumps({"default_branch": "main"}).encode())

    monkeypatch.setattr("wakey.agents.workspace.httpx.get", fake_get)
    runtime = WorkspaceRuntime(token="t", api_base="https://api.test", work_root=tmp_path)
    with pytest.raises(WorkspaceError, match="tarball fetch failed"):
        runtime.prepare("acme/fixture")


def test_bootstrap_runs_pip_steps(tmp_path, monkeypatch) -> None:
    """requirements.txt + pyproject.toml both trigger bounded pip steps."""
    seen: list[list[str]] = []

    def fake_run(workspace, command, timeout_s=300, network_ns=True):
        seen.append(command)
        return fix_mod.TestOutcome(False, "pip unavailable in sandbox")

    monkeypatch.setattr(ws_mod, "run_tests", fake_run)

    raw = make_tar(
        {
            "root/requirements.txt": b"requests\n",
            "root/pyproject.toml": b"[project]\nname = 'x'\n",
        }
    )
    extract_tarball_bytes(raw, tmp_path)
    runtime = WorkspaceRuntime(token=None, api_base="https://api.test", work_root=tmp_path)
    runtime._bootstrap(tmp_path)
    assert len(seen) == 2, "requirements + editable install both attempted"
    assert any("requirements.txt" in " ".join(c) for c in seen)
    assert any("-e" in " ".join(c) for c in seen)


def test_cleanup_removes_workspace(tmp_path, monkeypatch) -> None:
    called: list[Path] = []

    def record(path: Path) -> None:
        called.append(path)

    monkeypatch.setattr(ws_mod, "_rmtree", record)
    ws = tmp_path / "ws-dir"
    ws.mkdir()
    ws_mod.WorkspaceRuntime.cleanup(ws)
    assert called == [ws]
