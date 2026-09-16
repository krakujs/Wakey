# SPDX-License-Identifier: Apache-2.0
"""GitHub App setup routes (WF-01 §3, E3-T1): manifest, callback, storage.

The operator-facing flow has three steps:

1. ``GET /setup/github/start`` returns the pre-filled manifest and the
   github.com creation URL (admin session required).
2. The operator creates the app on github.com; GitHub redirects to
   ``GET /setup/github/callback?code=...``.
3. The callback exchanges the code for credentials and stores them —
   **fail-closed**: without a configured master key (WAKEY_MASTER_KEY)
   the private key is never persisted, encrypted or otherwise.

The exchange talks to ``settings.github_api_base`` so contract tests run
against the local simulator exactly like production runs against
github.com.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from wakey.core.storage import Storage
from wakey.forge.github_app import (
    AppExchangeError,
    build_manifest,
    creation_url,
    exchange_code,
)
from wakey.security.secretbox import SecretBox

_SECRET_SLUG = "gh_app:slug"
_SECRET_CLIENT_ID = "gh_app:client_id"
_SECRET_CLIENT_SECRET = "gh_app:client_secret"
_SECRET_PRIVATE_KEY = "gh_app:private_key"
_SECRET_WEBHOOK = "gh_app:webhook_secret"


class MissingSecretBox(RuntimeError):
    """High-value credentials cannot be stored without WAKEY_MASTER_KEY."""


def store_app_credentials(
    storage: Storage,
    credentials: object,
    secret_box: SecretBox | None,
) -> None:
    """Persist app credentials — private key and client secret encrypted.

    Fail-closed (SEC-4): without a configured SecretBox this raises
    instead of storing the private key in plaintext.
    """
    if secret_box is None:
        raise MissingSecretBox(
            "WAKEY_MASTER_KEY must be configured before connecting a GitHub App: "
            "the app private key must be stored encrypted, never in plaintext"
        )
    storage.set_secret(_SECRET_SLUG, str(getattr(credentials, "slug", "")))
    storage.set_secret(_SECRET_CLIENT_ID, str(getattr(credentials, "client_id", "")))
    storage.set_secret(
        _SECRET_CLIENT_SECRET,
        str(getattr(credentials, "client_secret", "")),
    )
    storage.set_secret(
        _SECRET_PRIVATE_KEY, secret_box.encrypt(str(getattr(credentials, "private_key_pem", "")))
    )
    storage.set_secret(
        _SECRET_WEBHOOK, secret_box.encrypt(str(getattr(credentials, "webhook_secret", "")))
    )


def app_connected(storage: Storage) -> bool:
    return storage.get_secret(_SECRET_PRIVATE_KEY) is not None


def register_github_app_routes(
    app: FastAPI,
    *,
    settings: Any,  # Settings; typed loosely to keep the module import-light
    storage: Storage,
    admin_guard: Callable[[Request], Response | None],
    secret_box: SecretBox | None,
    client_factory: Callable[[], httpx.Client] = httpx.Client,
) -> None:
    @app.get("/setup/github/start")
    def start_manifest(request: Request) -> Response:
        """Step 1: the pre-filled manifest + creation URL for the operator."""
        if (denied := admin_guard(request)) is not None:
            return denied
        manifest = build_manifest(base_url=str(settings.base_url))
        return JSONResponse({"creation_url": creation_url(manifest), "manifest": manifest})

    @app.get("/setup/github/callback")
    async def callback(request: Request) -> Response:
        """Step 2+3: exchange the redirect code, store credentials encrypted."""
        if (denied := admin_guard(request)) is not None:
            return denied
        code = str(request.query_params.get("code", ""))
        if not code:
            return JSONResponse({"error": "missing code parameter"}, status_code=400)
        try:
            credentials = exchange_code(
                code,
                api_base=str(settings.github_api_base),
                client=client_factory(),
            )
        except AppExchangeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        try:
            store_app_credentials(storage, credentials, secret_box)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(
            {"connected": True, "slug": html.escape(credentials.slug)}, status_code=201
        )
