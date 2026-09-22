# SPDX-License-Identifier: Apache-2.0
"""GitHub authority boundary for the Wakey-owned App.

Device authorization proves GitHub identity. Only repositories on which that
user is an administrator can be delegated to a long-running deployment.
Installation IDs are discovered through GitHub, never trusted from a client.
"""

from __future__ import annotations

from typing import Any

import httpx

from wakey.forge.connect_store import ConnectDenied
from wakey.forge.github_app import sign_app_jwt


class ConnectGitHub:
    def __init__(
        self, client_id: str, private_key: str, client: httpx.Client | None = None
    ) -> None:
        self.client_id = client_id
        self.private_key = private_key
        self.http = client or httpx.Client(timeout=15, follow_redirects=False)

    def device(self) -> dict[str, Any]:
        response = self.http.post(
            "https://github.com/login/device/code",
            data={"client_id": self.client_id},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        if not data.get("device_code") or not data.get("user_code"):
            raise ConnectDenied("GitHub device flow unavailable; check App configuration")
        return data

    def poll(self, device_code: str) -> dict[str, Any]:
        response = self.http.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": self.client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def _get(self, path: str, token: str) -> dict[str, Any]:
        response = self.http.get(
            "https://api.github.com" + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def repositories(self, token: str) -> tuple[int, list[dict[str, Any]]]:
        user = self._get("/user", token)
        candidates: list[dict[str, Any]] = []
        # Bound API work; do not silently ignore additional pages/installations.
        installs = self._get("/user/installations?per_page=100", token)
        if installs["total_count"] > 100:
            raise ConnectDenied("more than 100 installations; narrow App access before pairing")
        for installation in installs["installations"]:
            if installation.get("suspended_at"):
                continue
            iid = int(installation["id"])
            repos = self._get(f"/user/installations/{iid}/repositories?per_page=100", token)
            if repos["total_count"] > 100:
                raise ConnectDenied("select at most 100 repositories for this App installation")
            for repo in repos["repositories"]:
                if repo.get("permissions", {}).get("admin") is True:
                    candidates.append(
                        {"id": int(repo["id"]), "name": str(repo["full_name"]), "installation": iid}
                    )
        if not candidates:
            raise ConnectDenied("install the App on a repository you administer, then pair again")
        return int(user["id"]), candidates

    def token(self, repository: dict[str, Any]) -> dict[str, Any]:
        # No first-installation selection, and never an unscoped token request.
        response = self.http.post(
            f"https://api.github.com/app/installations/{int(repository['installation'])}/access_tokens",
            headers={
                "Authorization": f"Bearer {sign_app_jwt(self.private_key, self.client_id)}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "repository_ids": [int(repository["id"])],
                # deployments:read matches the App's deployment_status
                # subscription — deploy events drive verification (WF-08)
                "permissions": {
                    "contents": "write",
                    "issues": "write",
                    "pull_requests": "write",
                    "deployments": "read",
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        return {"token": str(data["token"]), "expires_at": str(data["expires_at"])}
