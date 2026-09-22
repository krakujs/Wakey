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

import base64
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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


class AppAuthError(Exception):
    """Runtime App authentication failed (JWT signing or token minting)."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign_app_jwt(private_key_pem: str, client_id: str, *, ttl_seconds: int = 540) -> str:
    """Sign an RS256 GitHub App JWT (RFC 8725 §3.1: asymmetric, short-lived).

    ``client_id`` is the JWT issuer — GitHub accepts it in place of the
    numeric app id. Requires the ``secrets`` extra (cryptography).
    """
    try:
        from cryptography.hazmat.primitives import (  # noqa: PLC0415 — optional extra
            hashes,
            serialization,
        )
        from cryptography.hazmat.primitives.asymmetric import (  # noqa: PLC0415 — optional extra
            padding,
        )
        from cryptography.hazmat.primitives.asymmetric.rsa import (  # noqa: PLC0415 — optional extra
            RSAPrivateKey,
        )
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppAuthError(
            "the cryptography package is required for GitHub App auth — "
            'install with pip install "wakey[secrets]"'
        ) from exc
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise AppAuthError(f"stored GitHub App private key is not valid PEM: {exc}") from exc
    if not isinstance(key, RSAPrivateKey):
        raise AppAuthError("GitHub App private key must be an RSA key (PEM PKCS#8)")
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps({"iss": client_id, "iat": now - 60, "exp": now + ttl_seconds}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256()))
    return f"{header}.{payload}.{signature}"


@dataclass(frozen=True)
class InstallationToken:
    """One minted App installation access token with its expiry (epoch s)."""

    token: str
    expires_at: float


INSTALLATIONS_PATH = "/app/installations"


def fetch_installation_token(
    private_key_pem: str,
    client_id: str,
    *,
    api_base: str = "https://api.github.com",
    client: httpx.Client | None = None,
) -> InstallationToken:
    """JWT → first installation → installation access token (WF-01 §3).

    The single-installation case is the supported self-hosted shape; with
    multiple installations the first is used and the operator should scope
    the App instead.
    """
    owned = client is None
    http = client or httpx.Client(timeout=15)
    try:
        auth = {"Authorization": f"Bearer {sign_app_jwt(private_key_pem, client_id)}"}
        listed = http.get(f"{api_base.rstrip('/')}{INSTALLATIONS_PATH}", headers=auth)
        if listed.status_code != 200:
            raise AppAuthError(f"installation lookup failed: HTTP {listed.status_code}")
        installations = listed.json()
        if not installations:
            raise AppAuthError(
                "the GitHub App has no installations — install it on the target account first"
            )
        installation_id = installations[0]["id"]
        minted = http.post(
            f"{api_base.rstrip('/')}{INSTALLATIONS_PATH}/{installation_id}/access_tokens",
            headers=auth,
        )
        if minted.status_code != 201:
            raise AppAuthError(f"installation token request failed: HTTP {minted.status_code}")
        data = minted.json()
    finally:
        if owned:
            http.close()
    expires_raw = str(data.get("expires_at", ""))
    try:
        expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise AppAuthError(f"installation token has unusable expiry {expires_raw!r}") from exc
    return InstallationToken(token=str(data.get("token", "")), expires_at=expires_at)


class InstallationTokenSource:
    """Mints App installation tokens and caches them until near expiry.

    GitHub installation tokens live for one hour; this source re-mints
    once the cached token is inside ``margin_seconds`` of expiry, so a
    long-running wakeyd never sends a stale credential.
    """

    def __init__(
        self,
        private_key_pem: str,
        client_id: str,
        *,
        api_base: str = "https://api.github.com",
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        margin_seconds: int = 60,
    ) -> None:
        self._private_key_pem = private_key_pem
        self._client_id = client_id
        self._api_base = api_base
        self._client = client
        self._clock = clock
        self._margin = margin_seconds
        self._lock = threading.Lock()
        self._cached: InstallationToken | None = None

    def token(self) -> str:
        with self._lock:
            now = self._clock()
            if self._cached is not None and now < self._cached.expires_at - self._margin:
                return self._cached.token
            self._cached = fetch_installation_token(
                self._private_key_pem,
                self._client_id,
                api_base=self._api_base,
                client=self._client,
            )
            return self._cached.token
