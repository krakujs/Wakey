# SPDX-License-Identifier: Apache-2.0
"""GitHub App manifest flow (WF-01 §3, ONB-3, E3-T1).

The self-hosted trust model (founder decision): the *user* owns the app.
The operator clicks "Connect GitHub", GitHub shows a pre-filled manifest
(issues RW, contents RW, pull-requests RW, metadata R), and after
creation redirects back to this instance with a temporary ``code``. The
code is exchanged — once — for the app's credentials via
``POST /app-manifests/{code}/conversions``.

This module is transport-only: the manifest builder and the code
exchange, with httpx injectable for contract tests. Storing the returned
private key is the caller's job and MUST go through
:class:`wakey.security.secretbox.SecretBox` (fail-closed, SEC-4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

APP_CREATION_URL = "https://github.com/settings/apps/new"
MANIFEST_CONVERSION_PATH = "/app-manifests/{code}/conversions"


class AppExchangeError(Exception):
    """The temporary code could not be exchanged for app credentials."""


@dataclass(frozen=True)
class GitHubAppCredentials:
    """Everything a GitHub App installation provides after conversion."""

    slug: str
    client_id: str
    client_secret: str
    private_key_pem: str
    webhook_secret: str


def build_manifest(
    *,
    base_url: str,
    app_name: str = "wakey",
) -> dict[str, object]:
    """The pre-filled app manifest: least privilege for the wakey loop."""
    base = base_url.rstrip("/")
    return {
        "name": app_name,
        "url": base,
        "hook_attributes": {
            "active": True,
            "url": f"{base}/webhooks/github",
        },
        "redirect_url": f"{base}/setup/github/callback",
        "callback_urls": [f"{base}/setup/github/callback"],
        "public": False,
        "default_permissions": {
            "issues": "write",
            "contents": "write",
            "pull_requests": "write",
            "metadata": "read",
        },
        "default_events": ["issues", "issue_comment", "pull_request", "push"],
    }


def creation_url(manifest: dict[str, object]) -> str:
    """The github.com URL the operator opens to create the pre-filled app."""
    query = urlencode({"manifest": json.dumps(manifest)})
    return f"{APP_CREATION_URL}?{query}"


def exchange_code(
    code: str,
    *,
    api_base: str = "https://api.github.com",
    client: httpx.Client | None = None,
) -> GitHubAppCredentials:
    """Exchange the one-time redirect code for app credentials (WF-01 §3)."""
    owned = client is None
    http = client or httpx.Client(timeout=15)
    try:
        response = http.post(
            f"{api_base.rstrip('/')}{MANIFEST_CONVERSION_PATH.format(code=code)}",
            json={},
        )
    finally:
        if owned:
            http.close()
    if response.status_code != 201:
        raise AppExchangeError(f"manifest exchange failed: HTTP {response.status_code}")
    data = response.json()
    credentials = GitHubAppCredentials(
        slug=str(data.get("slug", "")),
        client_id=str(data.get("client_id", "")),
        client_secret=str(data.get("client_secret", "")),
        private_key_pem=str(data.get("pem", "")),
        webhook_secret=str(data.get("webhook_secret", "")),
    )
    if not credentials.private_key_pem or not credentials.slug:
        raise AppExchangeError("conversion response missing slug or private key")
    return credentials
