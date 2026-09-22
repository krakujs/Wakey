# SPDX-License-Identifier: Apache-2.0
"""Public webhook inbox for a hosted Connect deployment (readiness #2/#6).

The single internet-facing surface in front of the IAM-restricted broker:
verify the GitHub HMAC signature with the shared webhook secret, then relay
the delivery to the broker with this instance's Google identity token. So
unsigned traffic dies here, the broker keeps IAM enforcement on every route
(including ``/v1/pair``), and GitHub needs no Google credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

METADATA = "http://metadata.google.internal"
MAX_DELIVERY_BYTES = 1024 * 1024  # mirrors the broker's webhook cap
FORWARD_TIMEOUT = 20.0


def read_webhook_secret() -> str:
    """Secret from a mounted file or the environment; empty fails closed."""
    path = os.environ.get("WAKEY_CONNECT_WEBHOOK_SECRET_FILE", "")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get("WAKEY_CONNECT_WEBHOOK_SECRET", "").strip()


def broker_url() -> str:
    value = os.environ.get("WAKEY_CONNECT_BROKER_URL", "").strip().rstrip("/")
    if not value.startswith("https://"):
        raise SystemExit("WAKEY_CONNECT_BROKER_URL must be the broker's HTTPS URL")
    return value


class IdentityToken:
    """Runtime Google identity token, cached until close to its expiry."""

    def __init__(self, audience: str, http: httpx.Client | None = None) -> None:
        self._audience = audience
        self._http = http or httpx.Client(timeout=10)
        self._token = ""
        self._expires = 0.0

    def get(self) -> str:
        if self._token and time.time() < self._expires - 120:
            return self._token
        response = self._http.get(
            f"{METADATA}/computeMetadata/v1/instance/service-accounts/default/identity",
            params={"audience": self._audience},
            headers={"Metadata-Flavor": "Google"},
        )
        response.raise_for_status()
        self._token = response.text.strip()
        # Metadata identity tokens are issued for ~3600s; the metadata
        # response carries no expiry header, so cache conservatively.
        self._expires = time.time() + 3000
        return self._token


def create_inbox_app(
    secret: str,
    broker: str,
    identity: IdentityToken,
    *,
    relay_client: httpx.Client | None = None,
) -> FastAPI:
    if not secret:
        raise SystemExit("webhook secret missing; refusing to serve an unverifiable endpoint")
    app = FastAPI(title="Wakey Connect Inbox", docs_url=None, redoc_url=None, openapi_url=None)
    client = relay_client or httpx.Client(timeout=FORWARD_TIMEOUT)

    @app.get("/")
    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def webhook(request: Request) -> JSONResponse:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_DELIVERY_BYTES:
                return JSONResponse({"error": "payload too large"}, status_code=413)
        signature = "sha256=" + hmac.new(secret.encode(), bytes(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, request.headers.get("X-Hub-Signature-256", "")):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        delivery = request.headers.get("X-GitHub-Delivery", "")
        if not delivery or len(delivery) > 200:
            return JSONResponse({"error": "delivery id required"}, status_code=400)
        forward = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in ("content-type", "x-github-delivery", "x-github-event")
            or key.lower().startswith("x-hub-")
        }
        response = client.post(
            broker + "/webhooks/github",
            content=bytes(body),
            headers={**forward, "X-Serverless-Authorization": "Bearer " + identity.get()},
        )
        if response.status_code >= 500:
            # Surface a retryable failure so GitHub redelivers instead of dropping.
            return JSONResponse(
                {"error": "broker unavailable; retry queued at GitHub"}, status_code=503
            )
        return JSONResponse(response.json(), status_code=response.status_code)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Wakey Connect webhook inbox")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8481)
    args = parser.parse_args()

    import uvicorn  # noqa: PLC0415 — server-only dependency path

    app = create_inbox_app(read_webhook_secret(), broker_url(), IdentityToken(broker_url()))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
