# SPDX-License-Identifier: Apache-2.0
"""A short, server-rendered GitHub pairing journey, with explicit trust disclosure."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from html import escape
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response

from wakey.core.models import AuditEvent
from wakey.core.storage import Storage
from wakey.forge.connect_client import ConnectClient
from wakey.forge.github import ForgeError
from wakey.security.secretbox import hash_token
from wakey.web.auth import SESSION_COOKIE
from wakey.web.style import STYLE


def render_connect(client: ConnectClient | None, csrf: str, message: str = "") -> str:
    def form(action: str, label: str, extra: str = "") -> str:
        return (
            f'<form method="post" action="/connect/{action}">'
            f'<input type="hidden" name="csrf" value="{escape(csrf)}">{extra}'
            f'<button class="btn" type="submit">{escape(label)}</button></form>'
        )

    state = client.state() if client else {}
    phase = state.get("state", "new")
    if client is None:
        body = (
            "<h2>Hosted connection is not configured on this build</h2>"
            "<p>The operator must set WAKEY_CONNECT_URL to the approved HTTPS service. "
            "No data has been sent to a hosted service.</p>"
        )
    elif phase == "connected":
        body = (
            '<h2>GitHub connected</h2><p class="mono">' + escape(client.repository()) + "</p>"
            '<p><a class="btn" href="/setup">Next: connect your service logs →</a></p>'
            '<p class="meta">Saved connection. Check access if permissions have changed.</p>'
            + form("poll", "Check GitHub connection")
            + form("disconnect", "Disconnect GitHub")
        )
    elif phase == "pending":
        # Both links stay on github.com. The local broker credential is never rendered.
        installation = str(state.get("installation_url", ""))
        if not installation.startswith("https://github.com/apps/"):
            installation = "https://github.com/settings/installations"
        body = (
            "<h2>1 · Install the Wakey App</h2>"
            "<p>Choose only the repositories you want to connect. "
            "Your organization may require owner approval.</p>"
            '<p><a class="btn" target="_blank" rel="noopener noreferrer" '
            f'href="{escape(installation)}">Install on GitHub ↗</a></p>'
            "<h2>2 · Confirm this deployment</h2><p>Enter this temporary code on GitHub. "
            "Never enter a code sent to you by somebody else.</p>"
            '<pre style="font-size:28px;letter-spacing:4px">'
            f"{escape(str(state.get('user_code', '')))}</pre>"
            '<p><a target="_blank" rel="noopener noreferrer" '
            'href="https://github.com/login/device">Authorize on GitHub ↗</a></p>'
            + form("poll", "I’ve authorized — check connection")
        )
    elif phase == "selecting":
        choices = "".join(
            f'<option value="{int(r["id"])}">{escape(r["name"])}</option>'
            for r in state.get("repositories", [])
        )
        body = (
            "<h2>3 · Choose a repository</h2><p>Only repositories you administer are shown. "
            "This version connects one repository per deployment.</p>"
            + form(
                "select",
                "Connect repository",
                '<label>Repository <select name="repository_id">'
                + choices
                + "</select></label><br><br>",
            )
        )
    else:
        body = "<h2>One App. Your deployment.</h2>" + form("begin", "Connect GitHub →")
    if phase in ("pending", "selecting"):
        body += "<p>Expired code or wrong account?</p>" + form("begin", "Start again")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Connect GitHub · Wakey</title><style>" + STYLE + "</style></head><body>"
        '<main style="max-width:760px"><nav><a href="/board">Wakey</a> / First-time setup</nav>'
        '<p class="meta" style="margin-top:28px">01 CONNECT · 02 SHIP LOGS · 03 WATCH</p>'
        "<h1>Your repositories. Wakey on watch.</h1>"
        "<p>No personal access token. No App private key to copy.</p>"
        + (f'<p role="status" class="flash">{escape(message)}</p>' if message else "")
        + '<section class="card" style="padding:24px">'
        + body
        + "</section>"
        '<p class="note">Wakey Connect handles GitHub authentication '
        "and relays repository webhooks. "
        "Production logs stay on your deployment. The shared App requires the hosted service. "
        "Automatic fixes remain opt-in.</p>"
        "<details><summary>Advanced: independent hosting</summary>"
        '<p><a href="/setup/github/start">Bring your own GitHub App</a> · '
        '<a href="/setup">Register a service</a></p></details></main></body></html>'
    )


def register_connect_routes(
    app: FastAPI,
    storage: Storage,
    client: ConnectClient | None,
    admin_guard: Callable[[Request], Response | None],
) -> None:
    def csrf(request: Request) -> str:
        return hash_token(request.cookies.get(SESSION_COOKIE, "") + ":wakey-connect")

    @app.get("/connect")
    def page(request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        return Response(
            render_connect(client, csrf(request)),
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/connect/{action}")
    async def change(action: str, request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        fields = parse_qs((await request.body()).decode())
        if not hmac.compare_digest(fields.get("csrf", [""])[0], csrf(request)):
            return Response("Invalid setup request. Reload the page.", status_code=403)
        if client is None:
            return Response("Connect endpoint not configured", status_code=503)
        # The form is async only for body parsing. Network operations run off-loop.
        import asyncio  # noqa: PLC0415

        def perform() -> str:
            try:
                if action == "begin":
                    client.begin()
                elif action == "poll":
                    client.poll()
                elif action == "select":
                    client.select(int(fields.get("repository_id", ["0"])[0]))
                elif action == "disconnect":
                    client.disconnect()
                else:
                    return "Unknown connection action"
                storage.record_audit(
                    AuditEvent(
                        actor="user:dashboard",
                        action="connect." + action,
                        subject="github",
                        details={},
                    )
                )
                return "Connection checked. Continue below."
            except (ForgeError, ValueError) as exc:
                return str(exc)

        message = await asyncio.to_thread(perform)
        return Response(
            render_connect(client, csrf(request), message),
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
