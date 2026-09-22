# SPDX-License-Identifier: Apache-2.0
"""GitHub Connect protocol contract and authority checks (no live credentials)."""

import json

import httpx
import pytest

from wakey.forge.connect_github import ConnectGitHub
from wakey.forge.connect_store import ConnectDenied


def test_device_flow_authority_and_repository_scoped_token(monkeypatch):
    calls = []

    def wire(request):
        calls.append(request)
        path = request.url.path
        if path == "/login/device/code":
            return httpx.Response(
                200, json={"device_code": "synthetic-device", "user_code": "TEST-CODE"}
            )
        if path == "/login/oauth/access_token":
            assert b"device_code=synthetic-device" in request.content
            return httpx.Response(200, json={"access_token": "synthetic-user"})
        if path == "/user":
            return httpx.Response(200, json={"id": 42})
        if path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "installations": [
                        {"id": 9, "suspended_at": "yesterday"},
                        {"id": 10, "suspended_at": None},
                    ],
                },
            )
        if path == "/user/installations/10/repositories":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "repositories": [
                        {
                            "id": 1,
                            "full_name": "synthetic/readonly",
                            "permissions": {"admin": False},
                        },
                        {"id": 2, "full_name": "synthetic/admin", "permissions": {"admin": True}},
                    ],
                },
            )
        assert path == "/app/installations/10/access_tokens"
        assert json.loads(request.content) == {
            "repository_ids": [2],
            "permissions": {
                "contents": "write",
                "issues": "write",
                "pull_requests": "write",
                "deployments": "read",
            },
        }
        assert request.headers["authorization"] == "Bearer synthetic-app-jwt"
        return httpx.Response(
            201, json={"token": "synthetic-installation", "expires_at": "2035-01-01T00:00:00Z"}
        )

    monkeypatch.setattr("wakey.forge.connect_github.sign_app_jwt", lambda *_: "synthetic-app-jwt")
    with httpx.Client(transport=httpx.MockTransport(wire)) as http:
        github = ConnectGitHub("synthetic-client", "never-distributed", http)
        github.device()
        assert github.poll("synthetic-device")["access_token"] == "synthetic-user"
        owner, repos = github.repositories("synthetic-user")
        assert owner == 42
        assert repos == [{"id": 2, "name": "synthetic/admin", "installation": 10}]
        assert github.token(repos[0])["token"] == "synthetic-installation"
    assert all("never-distributed" not in str(r.content) for r in calls)


@pytest.mark.parametrize("mode", ["installations", "repositories", "no-admin", "device"])
def test_fail_closed_on_incomplete_or_unauthorized_discovery(mode):
    def wire(request):
        if request.url.path == "/login/device/code":
            return httpx.Response(200, json={"error": "disabled"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 1})
        if request.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "total_count": 101 if mode == "installations" else 1,
                    "installations": [{"id": 1}],
                },
            )
        return httpx.Response(
            200, json={"total_count": 101 if mode == "repositories" else 0, "repositories": []}
        )

    with httpx.Client(transport=httpx.MockTransport(wire)) as http:
        github = ConnectGitHub("id", "private", http)
        with pytest.raises(ConnectDenied):
            github.device() if mode == "device" else github.repositories("synthetic")
