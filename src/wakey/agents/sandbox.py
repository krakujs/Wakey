# SPDX-License-Identifier: Apache-2.0
"""Fail-closed, per-attempt Docker execution for untrusted repository tests.

Images are provisioned by operators, never built or pulled from repository input.
Only a read-only checkout is mounted; execution uses a disposable tmpfs copy.
"""

from __future__ import annotations

import io
import json
import os
import selectors
import shutil
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

import httpx

from wakey.agents.fix import TestOutcome


class SandboxError(RuntimeError):
    """Infrastructure failure, not evidence that a reproduction failed."""


class DockerTestRunner:
    def __init__(
        self,
        image: str | None = None,
        *,
        timeout_s: float = 120,
        workspace_mode: str = "bind",
    ) -> None:
        self.image = os.environ.get("WAKEY_SANDBOX_IMAGE", "") if image is None else image
        self.timeout_s = timeout_s
        if workspace_mode not in ("bind", "copy"):
            raise SandboxError("workspace mode must be 'bind' or 'copy'")
        self.workspace_mode = workspace_mode

    def command(self, workspace: Path, argv: list[str], name: str) -> list[str]:
        """Return the local-run command (kept public for policy tests)."""
        return self._container_command(workspace, argv, name, stream_workspace=False)

    def _container_command(
        self, workspace: Path, argv: list[str], name: str, *, stream_workspace: bool
    ) -> list[str]:
        if not self.image or self.image.startswith("-"):
            raise SandboxError("WAKEY_SANDBOX_IMAGE must name a pre-provisioned test image")
        if not argv:
            raise SandboxError("sandbox test command is empty")
        source = str(workspace.resolve(strict=True))
        if "," in source:
            raise SandboxError("workspace path cannot contain a comma")
        command = [
            "docker",
            "run",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--log-driver",
            "none",
            "--tmpfs",
            "/workspace:rw,nosuid,size=128m,mode=1777",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--entrypoint",
            "/bin/sh",
            self.image,
            "-c",
            'cp -R /source/. /workspace/ || exit 125; exec "$@"',
            "wakey-test",
            *argv,
        ]
        if stream_workspace:
            image_at = command.index(self.image)
            command.insert(image_at, "--interactive")
            script_at = -(len(argv) + 2)
            command[script_at] = 'tar -xz -C /workspace && cd /workspace && exec "$@"'
        else:
            mount_at = command.index("--tmpfs")
            command[mount_at:mount_at] = [
                "--mount",
                f"type=bind,src={source},dst=/source,readonly",
            ]
        return command

    def __call__(self, workspace: Path, argv: list[str]) -> TestOutcome:  # noqa: PLR0912
        name = f"wakey-test-{uuid.uuid4().hex}"
        command = (
            self._container_command(workspace, argv, name, stream_workspace=True)
            if self.workspace_mode == "copy"
            else self.command(workspace, argv, name)
        )
        if shutil.which("docker") is None:
            raise SandboxError("Docker unavailable; host execution is forbidden")
        output = bytearray()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if self.workspace_mode == "copy" else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            if self.workspace_mode == "copy":
                assert process.stdin is not None
                try:
                    process.stdin.write(pack_workspace(workspace))
                    process.stdin.close()
                except OSError as exc:
                    raise SandboxError("sandbox workspace stream failed") from exc
                process.stdin = None
            deadline = time.monotonic() + self.timeout_s
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise SandboxError("sandbox test timeout; result is inconclusive")
                    for key, _ in selector.select(timeout=0.1):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            del output[:-6000]
            status = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if status in (125, 126, 127):
                raise SandboxError("sandbox could not start the configured test command")
            return TestOutcome(status == 0, output.decode("utf-8", errors="replace"))
        finally:
            # Kill the container as well as the Docker client, including timeout paths.
            try:
                subprocess.run(
                    ["docker", "rm", "--force", name], capture_output=True, timeout=10, check=False
                )
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
                    if process.stdout is not None:
                        process.stdout.close()


# --- remote isolated executor (production topology) ----------------------------------

MAX_WORKSPACE_UPLOAD_BYTES = 32 * 1024 * 1024


def pack_workspace(workspace: Path) -> bytes:
    """Pack a workspace directory into a gzipped tarball for remote execution.

    Regular files only, bounded total size — the executor re-applies its own
    extraction gates, so nothing here is a trust boundary, just hygiene.
    """
    buffer = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(workspace.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            total += len(data)
            if total > MAX_WORKSPACE_UPLOAD_BYTES:
                raise SandboxError("workspace exceeds the upload size cap")
            info = tarfile.TarInfo(path.relative_to(workspace).as_posix())
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class RemoteTestRunner:
    """Runs repository tests on a separate, narrowly-authorised executor host.

    The production topology (readiness #5): wakeyd never needs a container
    runtime. Test execution — image, mounts, resource caps — is enforced
    *by the executor*; the client ships only the workspace and the argv.
    Auth is a shared bearer token over HTTPS-capable HTTP.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout_s: float = 150,
    ) -> None:
        if not base_url or not token:
            raise SandboxError("remote executor requires a URL and a bearer token")
        self._token = token
        self._url = base_url.rstrip("/") + "/v1/run"
        self._client = client or httpx.Client(timeout=timeout_s)

    def __call__(self, workspace: Path, argv: list[str]) -> TestOutcome:
        source = str(workspace.resolve(strict=True))
        if not argv:
            raise SandboxError("sandbox test command is empty")
        tarball = pack_workspace(Path(source))
        try:
            response = self._client.post(
                self._url,
                files={"workspace": ("workspace.tar.gz", tarball, "application/gzip")},
                data={"argv": json.dumps(argv)},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            raise SandboxError(f"sandbox executor unreachable: {exc}") from exc
        if response.status_code != 200:
            raise SandboxError(f"sandbox executor rejected the run: HTTP {response.status_code}")
        payload = response.json()
        output = str(payload.get("output", ""))
        if not payload.get("ok", False):
            return TestOutcome(False, output or "executor reported a failed test run")
        return TestOutcome(True, output)
