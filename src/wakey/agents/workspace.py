# SPDX-License-Identifier: Apache-2.0
"""Workspace runtime (WF-06 §4, E7-T1 slice): a real checkout for the fixer.

Fetches the service repository as a tarball through the GitHub contents/
archive API (no git binary needed on slim images), extracts it behind a
safety gate (no path traversal, no symlink escapes, size caps), and
bootstraps a best-effort Python environment (``pip install`` of
requirements.txt / pyproject) with hard time and output bounds.

Honest limitations: the bootstrap suits pure-Python repos; repos needing
system packages or databases still fail their repro honestly, which the
fix flow reports instead of papering over.
"""

from __future__ import annotations

import io
import logging
import shutil
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from wakey.agents.fix import run_tests

logger = logging.getLogger(__name__)

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


class WorkspaceRuntime:
    """Fetches and prepares a repository workspace for the fixer."""

    def __init__(
        self,
        *,
        token: str | None,
        api_base: str = "https://api.github.com",
        work_root: Path,
        bootstrap_timeout_s: int = 240,
    ) -> None:
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._work_root = work_root
        self._bootstrap_timeout_s = bootstrap_timeout_s
        self._work_root.mkdir(parents=True, exist_ok=True)

    def prepare(self, repo: str) -> Workspace:
        """Download and extract ``repo``'s default branch into a fresh dir."""
        default_branch = self._default_branch(repo)
        workspace = self._work_root / f"{repo.replace('/', '__')}-{default_branch}"
        if workspace.exists():
            _rmtree(workspace)
        workspace.mkdir(parents=True)
        response = httpx.get(
            f"{self._api_base}/repos/{repo}/tarball/{default_branch}",
            headers=self._headers("application/vnd.github+json"),
            timeout=60,
            follow_redirects=True,
        )
        if response.status_code != 200:
            raise WorkspaceError(
                f"tarball fetch failed for {repo}@{default_branch}: HTTP {response.status_code}"
            )
        extract_tarball_bytes(response.content, workspace)
        self._bootstrap(workspace)
        return Workspace(path=workspace, default_branch=default_branch)

    # --- fetch ------------------------------------------------------------------

    def _headers(self, extra: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": extra, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _default_branch(self, repo: str) -> str:
        response = httpx.get(f"{self._api_base}/repos/{repo}", headers=self._headers(), timeout=15)
        if response.status_code != 200:
            raise WorkspaceError(f"repo {repo} unreachable: HTTP {response.status_code}")
        return str(response.json()["default_branch"] or "main")

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

    # --- bootstrap ----------------------------------------------------------------

    def _bootstrap(self, workspace: Path) -> None:
        """Best-effort dependency install, bounded; failures are tolerated."""
        steps: list[list[str]] = []
        if (workspace / "requirements.txt").exists():
            steps.append(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--no-input",
                    "-r",
                    "requirements.txt",
                ]
            )
        if (workspace / "pyproject.toml").exists() or (workspace / "setup.py").exists():
            steps.append(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--no-input",
                    "-e",
                    str(workspace),
                ]
            )
        for step in steps:
            outcome = run_tests(workspace, step, timeout_s=self._bootstrap_timeout_s)
            if not outcome.ok:
                logger.warning("bootstrap step failed (tolerated): %s", outcome.output[-300:])

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
            extracted.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:  # pragma: no cover — isfile guarantees a file
                raise WorkspaceError(f"could not read tarball member: {member.name}")
            with source:
                extracted.write_bytes(source.read())


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
