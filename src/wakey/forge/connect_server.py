# SPDX-License-Identifier: Apache-2.0
"""Wakey Connect API: verified pairing, scoped credentials, outbound relay.

Separate process from wakeyd. All operator secrets enter through configuration;
no deployment ever receives the App private key or another tenant's event.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from wakey.forge.connect_github import ConnectGitHub
from wakey.forge.connect_store import ConnectDenied, ConnectStore
from wakey.security.redaction import RedactionEngine


class Selection(BaseModel):
    repository_id: int = Field(gt=0)


class Acknowledgement(BaseModel):
    delivery_id: str = Field(min_length=1, max_length=200)


def bearer(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer ") or len(value) > 200:
        raise ConnectDenied("deployment credential required")
    return value[7:]


def create_connect_app(  # noqa: PLR0915 — one small route per protocol operation
    store: ConnectStore, github: ConnectGitHub, *, app_slug: str, webhook_secret: str
) -> FastAPI:
    if not re.fullmatch(r"[a-zA-Z0-9-]+", app_slug) or not webhook_secret:
        raise ValueError("public App slug and webhook secret are required")
    app = FastAPI(title="Wakey Connect", docs_url=None, redoc_url=None)
    redactor = RedactionEngine()

    @app.middleware("http")
    async def no_cache(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ConnectDenied)
    async def denied(request: Request, exc: ConnectDenied) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=403)

    @app.exception_handler(httpx.HTTPError)
    async def unavailable(request: Request, exc: httpx.HTTPError) -> JSONResponse:
        return JSONResponse(
            {"error": "GitHub unavailable or access revoked; reconnect if needed"}, status_code=503
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def ready() -> JSONResponse:
        try:
            with store.lock:
                store.db.execute("SELECT 1").fetchone()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.post("/v1/pair")
    def pair() -> dict[str, Any]:
        store.admit_pairing()
        data = github.device()
        interval = max(5, int(data.get("interval", 5)))
        ttl = min(900, int(data.get("expires_in", 900)))
        credential = store.create({"device_code": data["device_code"], "interval": interval}, ttl)
        return {
            "credential": credential,
            "user_code": data["user_code"],
            "expires_in": ttl,
            "interval": interval,
            "verification_uri": "https://github.com/login/device",
            "installation_url": f"https://github.com/apps/{app_slug}/installations/new",
        }

    @app.post("/v1/poll")
    def poll(request: Request) -> dict[str, Any]:
        record = store.read(bearer(request))
        data = record["data"]
        if record["state"] == "pending" and store.claim_poll(record):
            reply = github.poll(str(data["device_code"]))
            if reply.get("error") == "slow_down":
                data["interval"] += 5
                store.write(record, "pending", data)
            elif reply.get("error") == "authorization_pending":
                pass
            elif reply.get("access_token"):
                owner, repositories = github.repositories(str(reply["access_token"]))
                # Discard OAuth token/device code after deriving verified choices.
                store.write(record, "selecting", {"owner": owner, "repositories": repositories})
            else:
                store.revoke_id(record["id"])
                raise ConnectDenied("GitHub authorization expired or denied; start again")
            record = store.read(bearer(request))
        return {
            "state": record["state"],
            "repositories": record["data"].get("repositories", []),
            "repository": record["data"].get("repository"),
            "interval": record["data"].get("interval", 5),
        }

    @app.post("/v1/select")
    def select(request: Request, selection: Selection) -> dict[str, Any]:
        record = store.read(bearer(request))
        if record["state"] != "selecting":
            raise ConnectDenied("pairing is not ready for repository selection")
        repo = next(
            (r for r in record["data"]["repositories"] if r["id"] == selection.repository_id), None
        )
        if repo is None:
            raise ConnectDenied("repository was not authorized by this GitHub user")
        # Probe actual installation permissions before marking the connection usable.
        github.token(repo)
        store.write(record, "connected", {"owner": record["data"]["owner"], "repository": repo})
        return {"state": "connected", "repository": repo}

    @app.post("/v1/token")
    def token(request: Request) -> dict[str, Any]:
        record = store.read(bearer(request))
        if record["state"] != "connected":
            raise ConnectDenied("deployment is not connected")
        minted = github.token(record["data"]["repository"])
        store.read(bearer(request))  # fail closed if revoked while GitHub was issuing
        store.audit(record["id"], "token.issued")
        return minted

    @app.get("/v1/events")
    def events(request: Request) -> dict[str, Any]:
        return {"events": store.events(bearer(request))}

    @app.post("/v1/ack")
    def ack(request: Request, message: Acknowledgement) -> dict[str, bool]:
        store.acknowledge(bearer(request), message.delivery_id)
        return {"acknowledged": True}

    @app.post("/v1/disconnect")
    def disconnect(request: Request) -> dict[str, bool]:
        store.revoke(bearer(request))
        return {"disconnected": True}

    @app.post("/webhooks/github")
    async def webhook(request: Request) -> JSONResponse:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                return JSONResponse({"error": "payload too large"}, status_code=413)
        signature = "sha256=" + hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, request.headers.get("X-Hub-Signature-256", "")):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        delivery = request.headers.get("X-GitHub-Delivery", "")
        kind = request.headers.get("X-GitHub-Event", "")
        if not delivery or len(delivery) > 200:
            return JSONResponse({"error": "delivery id required"}, status_code=400)
        try:
            event = json.loads(body)
            if not isinstance(event, dict):
                raise ValueError("object required")
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        clean, _ = redactor.redact(json.dumps({"event": event, "kind": kind}))
        envelope = json.loads(clean)
        removed = event.get("repositories_removed", [])
        if (
            any(
                not isinstance(event.get(k, {}), dict)
                for k in ("installation", "sender", "repository")
            )
            or not isinstance(removed, list)
            or any(not isinstance(r, dict) for r in removed)
        ):
            return JSONResponse({"error": "invalid event shape"}, status_code=400)
        await asyncio.to_thread(_relay, store, kind, event, envelope, delivery)
        return JSONResponse({"accepted": True}, status_code=202)

    return app


def _relay(
    store: ConnectStore, kind: str, event: dict[str, Any], envelope: dict[str, Any], delivery: str
) -> None:
    """Database operations stay off the ASGI event loop, including remote Postgres."""
    for record in store.connected():
        repo = record["data"]["repository"]
        installation = event.get("installation", {}).get("id")
        removed = [r.get("id") for r in event.get("repositories_removed", [])]
        revoke = (
            kind == "github_app_authorization"
            and event.get("action") == "revoked"
            and event.get("sender", {}).get("id") == record["data"]["owner"]
        )
        if installation == repo["installation"]:
            revoke = revoke or (
                kind == "installation" and event.get("action") in ("deleted", "suspend")
            )
            revoke = revoke or repo["id"] in removed
        if revoke:
            store.revoke_id(record["id"])
        elif (
            installation == repo["installation"]
            and event.get("repository", {}).get("id") == repo["id"]
        ):
            store.enqueue(record["id"], delivery, envelope)
