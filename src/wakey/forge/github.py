# SPDX-License-Identifier: Apache-2.0
"""GitHub adapter for the ForgePort (WF-15).

The only module allowed to speak the GitHub REST API. Auth: fine-grained
PAT or App installation token passed in — this adapter is transport-only.
Errors are typed so the pipeline can distinguish "misconfigured" from
"rate-limited" from "broken".
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable

import httpx

from wakey.core.models import Fingerprint
from wakey.forge.port import (
    ForgeAuthError,
    ForgeError,
    ForgeRateLimited,
    ForgeUnreachable,
    TicketRef,
)

__all__ = [
    "ForgeAuthError",
    "ForgeError",
    "ForgeRateLimited",
    "ForgeUnreachable",
    "GitHubAdapter",
    "GitHubConfig",
]


class GitHubConfig:
    """Adapter configuration; ``allowed_repos`` is the hard write-guard."""

    def __init__(
        self,
        token: str,
        repo: str,
        api_base: str = "https://api.github.com",
        max_retries: int = 2,
        allowed_repos: tuple[str, ...] | None = None,
    ) -> None:
        if allowed_repos is not None and repo not in allowed_repos:
            msg = f"repo {repo!r} is not in the allowlist {allowed_repos}"
            raise ForgeError(msg)
        self.token = token
        self.repo = repo
        self.api_base = api_base
        self.max_retries = max_retries
        self.allowed_repos = allowed_repos


class GitHubAdapter:
    """Minimal ForgePort implementation against the GitHub REST API.

    Auth is a static token (PAT) or, for GitHub App installations, a
    ``token_provider`` callable that supplies a fresh installation token
    per request — the provider wins when both are set.
    """

    def __init__(
        self,
        config: GitHubConfig,
        client: httpx.Client | None = None,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._token = config.token
        self._token_provider = token_provider
        self._repo = config.repo
        self._api_base = config.api_base.rstrip("/")
        self._client = client or httpx.Client(timeout=15)
        self._max_retries = config.max_retries

    def _request(
        self,
        method: str,
        path: str,
        json_body: object,
        accept: str = "application/vnd.github+json",
    ) -> httpx.Response:
        url = f"{self._api_base}{path}"
        token = self._token_provider() if self._token_provider is not None else self._token
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, url, json=json_body, headers=headers)
            except httpx.HTTPError as exc:
                raise ForgeUnreachable(
                    f"github {method} {path} unreachable (write may have landed): {exc}"
                ) from exc
            if response.status_code == 401:
                raise ForgeAuthError("github token rejected (401)", status_code=401)
            if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
                raise ForgeRateLimited("github primary rate limit exhausted", status_code=403)
            if response.status_code >= 500 and attempt < self._max_retries:
                time.sleep(0.5 * (attempt + 1))  # bounded backoff
                continue
            if response.is_error:
                raise ForgeError(
                    f"github {method} {path} -> {response.status_code}",
                    status_code=response.status_code,
                )
            return response
        raise ForgeError(f"github {method} {path} failed after retries", status_code=599)

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
        response = self._request(
            "POST",
            f"/repos/{self._repo}/issues",
            {"title": title, "body": body, "labels": ["wakey"]},
        )
        data = response.json()
        return TicketRef(issue_id=str(data["number"]), url=data["html_url"])

    def find_ticket_by_marker(self, marker: str) -> TicketRef | None:
        """Open wakey-labeled issue carrying ``marker`` in its body (R-15).

        Bounded scan (3 pages): reconciliation runs only on the ambiguous
        failure path, and wakey labels every ticket it creates.
        """
        for page in (1, 2, 3):
            response = self._request(
                "GET",
                f"/repos/{self._repo}/issues?state=open&labels=wakey&per_page=100&page={page}",
                None,
            )
            issues = response.json()
            for issue in issues:
                if "pull_request" in issue:  # GitHub lists PRs here too
                    continue
                if marker in str(issue.get("body", "")):
                    return TicketRef(issue_id=str(issue["number"]), url=str(issue["html_url"]))
            if len(issues) < 100:
                break
        return None

    def add_comment(self, ticket: TicketRef, body: str) -> None:
        self._request(
            "POST", f"/repos/{self._repo}/issues/{ticket.issue_id}/comments", {"body": body}
        )

    def open_draft_proposal(self, branch: str, title: str, body: str, base: str) -> TicketRef:
        """Draft PR from a published branch (E8-T5/R-06); never merged by Wakey.

        Idempotent per head branch (R-15): a delivery retried after a lost
        response binds to the already-open draft instead of opening a twin.
        """
        existing = self._find_open_pull_by_head(branch)
        if existing is not None:
            return existing
        response = self._request(
            "POST",
            f"/repos/{self._repo}/pulls",
            {"title": title, "head": branch, "base": base, "draft": True, "body": body},
        )
        data = response.json()
        return TicketRef(issue_id=str(data["number"]), url=data["html_url"])

    def _find_open_pull_by_head(self, branch: str) -> TicketRef | None:
        for page in (1, 2, 3):
            response = self._request(
                "GET", f"/repos/{self._repo}/pulls?state=open&per_page=100&page={page}", None
            )
            pulls = response.json()
            for pull in pulls:
                head = pull.get("head")
                ref = head.get("ref", "") if isinstance(head, dict) else str(head or "")
                if ref == branch:
                    return TicketRef(issue_id=str(pull["number"]), url=str(pull["html_url"]))
            if len(pulls) < 100:
                break
        return None

    # --- branch publication (R-06) -------------------------------------------

    def default_branch(self) -> str:
        response = self._request("GET", f"/repos/{self._repo}", None)
        return str(response.json()["default_branch"])

    def create_branch(self, branch: str, base: str) -> None:
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", base):
            base_sha = base
        else:
            ref = self._request("GET", f"/repos/{self._repo}/git/ref/heads/{base}", None).json()
            base_sha = ref["object"]["sha"]
        try:
            self._request(
                "POST",
                f"/repos/{self._repo}/git/refs",
                {"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
        except ForgeError as exc:
            # already exists = our own earlier attempt (crash/retry after the
            # ref landed): resuming that branch is the idempotent outcome (R-15)
            if "422" not in str(exc):
                raise

    def commit_files(self, branch: str, message: str, files: dict[str, str]) -> str:
        """Publish full file contents as one commit on ``branch``.

        Git data API: blobs → tree (based on the branch tip) → commit →
        fast-forward the ref. ``force`` is always False: no force-pushes.
        """
        tip = self._request("GET", f"/repos/{self._repo}/git/ref/heads/{branch}", None).json()[
            "object"
        ]["sha"]
        base_tree = self._request("GET", f"/repos/{self._repo}/git/commits/{tip}", None).json()[
            "tree"
        ]["sha"]
        tree_items = []
        for path, content in files.items():
            blob = self._request(
                "POST",
                f"/repos/{self._repo}/git/blobs",
                {"content": content, "encoding": "utf-8"},
            ).json()
            tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self._request(
            "POST",
            f"/repos/{self._repo}/git/trees",
            {"base_tree": base_tree, "tree": tree_items},
        ).json()
        commit = self._request(
            "POST",
            f"/repos/{self._repo}/git/commits",
            {"message": message, "tree": tree["sha"], "parents": [tip]},
        ).json()
        self._request(
            "PATCH",
            f"/repos/{self._repo}/git/refs/heads/{branch}",
            {"sha": commit["sha"], "force": False},
        )
        return str(commit["sha"])

    def fetch_file(self, path: str) -> str | None:
        """Fetch file content from the repo root (E3-T6 config reload).

        Uses the contents API (base64 JSON) so local simulators and the
        real API behave identically.
        """
        try:
            response = self._request(
                "GET", f"/repos/{self._repo}/contents/{path.lstrip('/')}", None
            )
        except ForgeError as exc:
            if "404" in str(exc):
                return None
            raise
        data = response.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8")
        return str(content)

    def compare_commits(self, base: str, head: str) -> str | None:
        """Fetch a bounded unified diff between two deployment SHAs."""
        try:
            response = self._request(
                "GET",
                f"/repos/{self._repo}/compare/{base}...{head}",
                None,
                accept="application/vnd.github.v3.diff",
            )
        except ForgeError as exc:
            if "404" in str(exc) or "422" in str(exc):
                return None
            raise
        return response.text[:20000]

    # --- lifecycle effects (R-09) ----------------------------------------------

    def close_ticket(self, ticket: TicketRef, comment: str | None = None) -> None:
        if comment:
            self.add_comment(ticket, comment)
        self._request("PATCH", f"/repos/{self._repo}/issues/{ticket.issue_id}", {"state": "closed"})

    def reopen_ticket(self, ticket: TicketRef, comment: str | None = None) -> None:
        if comment:
            self.add_comment(ticket, comment)
        self._request("PATCH", f"/repos/{self._repo}/issues/{ticket.issue_id}", {"state": "open"})

    def add_label(self, ticket: TicketRef, label: str) -> None:
        self._request(
            "POST",
            f"/repos/{self._repo}/issues/{ticket.issue_id}/labels",
            {"labels": [label]},
        )
