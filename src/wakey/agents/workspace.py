# SPDX-License-Identifier: Apache-2.0
"""Workspace runtime (WF-06 §4, E7-T1 slice): a real checkout for the fixer.

Fetches the service repository as a tarball through the GitHub contents/
archive API (no git binary needed on slim images), extracts it behind a
safety gate (no path traversal, no symlink escapes, size caps).
Dependencies must be provisioned in the operator-owned sandbox image;
repository installation hooks are never executed on the application host.
"""

from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

MAX_TARBALL_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_FILES = 20_000


class WorkspaceError(Exception):
    """The repository could not be fetched or safely extracted."""


@dataclass(frozen=True)
class Workspace:
    """A prepared checkout plus what the fix flow needs to publish from it."""

    path: Path
    default_branch: str
    base_sha: str = ""


class WorkspaceRuntime:
    """Fetches and prepares a repository workspace for the fixer."""

    def __init__(
        self,
        *,
        token: str | None,
        api_base: str = "https://api.github.com",
        work_root: Path,
    ) -> None:
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._work_root = work_root
        self._work_root.mkdir(parents=True, exist_ok=True)

    def prepare(self, repo: str, ref: str | None = None) -> Workspace:
        """Download an exact branch tip into a fresh, isolated directory."""
        default_branch, base_sha = self._repository_ref(repo, ref)
        workspace = Path(tempfile.mkdtemp(prefix="wakey-", dir=self._work_root))
        try:
            self._extract_tarball(repo, base_sha, workspace)
            # The sandbox uses an unprivileged user and a read-only mount.
            workspace.chmod(0o755)
            return Workspace(path=workspace, default_branch=default_branch, base_sha=base_sha)
        except BaseException:
            _rmtree(workspace)
            raise

    # --- fetch ------------------------------------------------------------------

    def _headers(self, extra: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": extra, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _repository_ref(self, repo: str, ref_name: str | None) -> tuple[str, str]:
        response = httpx.get(f"{self._api_base}/repos/{repo}", headers=self._headers(), timeout=15)
        if response.status_code != 200:
            raise WorkspaceError(f"repo {repo} unreachable: HTTP {response.status_code}")
        default_branch = str(response.json()["default_branch"] or "main")
        branch = ref_name or default_branch
        ref = httpx.get(
            f"{self._api_base}/repos/{repo}/git/ref/heads/{quote(branch, safe='')}",
            headers=self._headers(),
            timeout=15,
        )
        if ref.status_code != 200:
            raise WorkspaceError(f"repo {repo} default ref unreachable: HTTP {ref.status_code}")
        sha = str(ref.json().get("object", {}).get("sha", ""))
        if len(sha) < 40:
            raise WorkspaceError(f"repo {repo} default ref returned an invalid commit")
        return default_branch, sha

    def _extract_tarball(self, repo: str, ref: str, workspace: Path) -> None:
        response = httpx.get(
            f"{self._api_base}/repos/{repo}/tarball/{ref}",
            headers=self._headers("application/vnd.github+json"),
            timeout=60,
            follow_redirects=True,
        )
        if response.status_code != 200:
            raise WorkspaceError(
                f"tarball fetch failed for {repo}@{ref}: HTTP {response.status_code}"
            )
        extract_tarball_bytes(response.content, workspace)

    @staticmethod
    def cleanup(workspace: Path) -> None:
        _rmtree(workspace)


def extract_tarball_bytes(raw: bytes, workspace: Path) -> None:
    """Extract a gzipped repo tarball behind the safety gates (R-03).

    Rejects oversize archives, path traversal, symlink/hardlink members,
    and extraction beyond file/size caps.
    """
    if len(raw) > MAX_TARBALL_BYTES:
        raise WorkspaceError("repository tarball exceeds the size cap")
    total = 0
    files = 0
    root_prefix = None
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive.getmembers():
            target = (workspace / member.name).resolve()
            if workspace.resolve() not in target.parents and target != workspace.resolve():
                raise WorkspaceError(f"tarball member escapes workspace: {member.name}")
            if member.issym() or member.islnk():
                raise WorkspaceError(f"tarball contains a link: {member.name}")
            if not member.isfile():
                continue
            if root_prefix is None:
                root_prefix = member.name.split("/", 1)[0] + "/"
            total += member.size
            files += 1
            if total > MAX_EXTRACTED_BYTES or files > MAX_EXTRACTED_FILES:
                raise WorkspaceError("repository exceeds extraction caps")
            extracted = workspace / member.name
            if root_prefix and member.name.startswith(root_prefix):
                extracted = workspace / member.name[len(root_prefix) :]
            if not extracted.resolve().is_relative_to(workspace.resolve()):
                raise WorkspaceError(f"stripped tarball path escapes workspace: {member.name}")
            extracted.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:  # pragma: no cover — isfile guarantees a file
                raise WorkspaceError(f"could not read tarball member: {member.name}")
            with source:
                extracted.write_bytes(source.read())


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
