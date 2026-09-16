# SPDX-License-Identifier: Apache-2.0
"""GitHub adapter for the ForgePort (WF-15).

The only module allowed to speak the GitHub REST API. Auth: fine-grained
PAT or App installation token passed in — this adapter is transport-only.
Errors are typed so the pipeline can distinguish "misconfigured" from
"rate-limited" from "broken".
"""

from __future__ import annotations

import time

import httpx

from wakey.core.models import Fingerprint
from wakey.forge.port import TicketRef


class ForgeError(Exception):
    """Base for GitHub API failures after bounded retries."""


class ForgeAuthError(ForgeError):
    """Token rejected or permission narrowed (401/403 non-rate)."""


class ForgeRateLimited(ForgeError):
    """Primary or secondary rate limit hit; backoff was exhausted."""


class GitHubAdapter:
    """Minimal ForgePort implementation against the GitHub REST API."""

    def __init__(
        self,
        token: str,
        repo: str,
        api_base: str = "https://api.github.com",
        client: httpx.Client | None = None,
        max_retries: int = 2,
        allowed_repos: tuple[str, ...] | None = None,
    ) -> None:
        if allowed_repos is not None and repo not in allowed_repos:
            raise ForgeError(f"repo {repo!r} is not in the allowlist {allowed_repos}")
        self._token = token
        self._repo = repo
        self._allowed_repos = allowed_repos
        self._api_base = api_base.rstrip("/")
        self._client = client or httpx.Client(timeout=15)
        self._max_retries = max_retries

    def _request(self, method: str, path: str, json_body: object) -> httpx.Response:
        url = f"{self._api_base}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        for attempt in range(self._max_retries + 1):
            response = self._client.request(method, url, json=json_body, headers=headers)
            if response.status_code == 401:
                raise ForgeAuthError("github token rejected (401)")
            if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
                raise ForgeRateLimited("github primary rate limit exhausted")
            if response.status_code >= 500 and attempt < self._max_retries:
                time.sleep(0.5 * (attempt + 1))  # bounded backoff
                continue
            if response.is_error:
                raise ForgeError(f"github {method} {path} -> {response.status_code}")
            return response
        raise ForgeError(f"github {method} {path} failed after retries")

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
        response = self._request(
            "POST",
            f"/repos/{self._repo}/issues",
            {"title": title, "body": body, "labels": ["wakey"]},
        )
        data = response.json()
        return TicketRef(issue_id=str(data["number"]), url=data["html_url"])

    def add_comment(self, ticket: TicketRef, body: str) -> None:
        self._request(
            "POST", f"/repos/{self._repo}/issues/{ticket.issue_id}/comments", {"body": body}
        )
