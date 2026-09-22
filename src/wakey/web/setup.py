# SPDX-License-Identifier: Apache-2.0
"""Setup wizard routes (WF-12 page 1/2, WF-01 §5, E3-T5).

Server-rendered, no JavaScript (WF-12 air-gap rule). The operator logs in
with the banner setup token, registers a service (repo, environment), and
receives the ingest key **exactly once** — only its hash is stored. The
same routes exist as JSON under ``/api/services`` for the CLI (WF-13).
"""

from __future__ import annotations

import html
import shlex
import urllib.parse
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from wakey.core.config import ServiceYaml
from wakey.core.models import AuditEvent
from wakey.core.storage import Storage
from wakey.forge.github import ForgeError
from wakey.web.registration import RegistrationError, register_service, rotate_ingest_key
from wakey.web.style import STYLE

_FORM_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wakey — register a service</title>
<style>%(style)s</style>
</head>
<body>
<main>
<nav><a href="/connect">Connect GitHub</a> · <a href="/board">Incident board</a></nav>
<h1>wakey setup — register a service</h1>
<p class="meta">Connect a deployable to its repository. You get an ingest key
<strong>shown once</strong> — point your log shipper at
<code>POST /ingest/&lt;key&gt;</code>.</p>
%(services_block)s
<div class="card">
<form method="post" action="/setup">
  <label>Service name <input name="name" value="%(name)s"
    placeholder="payments-api" required></label>
  <label>Repository (owner/name) <input name="repo" value="%(repo)s" %(readonly)s
    placeholder="acme/payments" required></label>
  <label>Environment <input name="environment" value="prod"></label>
  <label>Webhook secret (optional) <input name="webhook_secret" type="password"></label>
  <button type="submit">Register service</button>
</form>
%(problems)s
</div>
</main>
</body>
</html>
"""

_KEY_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wakey — service registered</title>
<style>%(style)s</style>
</head>
<body>
<main>
<h1>%(name)s registered</h1>
<p class="meta">Copy the ingest key now — it is <strong>never shown again</strong>.
(You can rotate it any time from this page.)</p>
<div class="card"><code class="key">%(key)s</code></div>
<p>Send logs: <code>POST /ingest/%(key)s</code> — JSON lines, logfmt, or plain text.</p>
<h2>Try your first signal</h2>
<p>Run this command on a machine that can reach Wakey. It sends three identical
synthetic errors, enough for the default ticket threshold. Treat the command as
a secret: it includes your ingest key and may enter your shell history.</p>
<pre style="white-space:pre-wrap;overflow-wrap:anywhere">%(command)s</pre>
<p class="meta">HTTP 202 means queued, not processed. Open the board and check for
the incident. A GitHub issue requires a working GitHub connection. Without one,
Wakey uses a local console demo. Custom thresholds can delay ticket creation.</p>
<p>For production logs, configure your log shipper to POST to the same endpoint.
Remote senders need a reachable HTTPS address configured as WAKEY_BASE_URL;
localhost only works on the Wakey host. Keep the dashboard behind authentication.</p>
<p><a href="/setup">Register another service</a> · <a href="/board">Go to board</a></p>
</main>
</body>
</html>
"""


def _services_list(services: list[Any]) -> str:
    if not services:
        return '<p class="meta">No services registered yet — this install cannot ingest.</p>'
    rows = "".join(
        f"<tr><td>{html.escape(s.name)}</td><td>{html.escape(s.repo)}</td>"
        f"<td>{html.escape(s.environment)}</td>"
        f'<td><form method="post" action="/setup/rotate/{html.escape(s.name)}">'
        '<button type="submit">rotate key</button></form></td></tr>'
        for s in services
    )
    return (
        '<div class="card"><table><thead><tr><th>service</th><th>repo</th>'
        "<th>environment</th><th></th></tr></thead><tbody>" + rows + "</tbody></table></div>"
    )


def _render_form(storage: Storage, problems: list[str] | None = None, repo: str = "") -> str:
    problem_block = (
        "".join(f'<p class="problem">{html.escape(problem)}</p>' for problem in problems)
        if problems
        else ""
    )
    return _FORM_PAGE % {
        "style": STYLE,
        "services_block": _services_list(storage.list_services()),
        "problems": problem_block,
        "repo": html.escape(repo, quote=True),
        "readonly": "readonly" if repo else "",
        "name": html.escape(repo.rsplit("/", 1)[-1].replace(".", "-")[:64], quote=True),
    }


def _key_page(name: str, key: str, base_url: str) -> Response:
    sample = '{"level":"error","msg":"Wakey setup test: synthetic connection failure"}\n'
    command = (
        "curl --fail-with-body -X POST "
        + shlex.quote(base_url.rstrip("/") + "/ingest/" + key)
        + " -H 'Content-Type: application/x-ndjson' --data-binary "
        + shlex.quote(sample * 3)
    )
    page = _KEY_PAGE % {
        "style": STYLE,
        "name": html.escape(name),
        "key": html.escape(key),
        "command": html.escape(command),
    }
    return Response(
        page,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


def register_setup_routes(  # noqa: PLR0915 — related registration routes share their guards
    app: FastAPI,
    *,
    storage: Storage,
    admin_guard: Callable[[Request], Response | None],
    connected_repo: Callable[[], str] | None = None,
    base_url: str = "http://localhost:8477",
) -> None:
    """Registration wizard + JSON API (admin-session required, R-07)."""

    def check_repo(repo: str) -> None:
        if connected_repo is not None:
            try:
                expected = connected_repo()
            except ForgeError as exc:
                raise RegistrationError([str(exc)]) from exc
            if repo.strip() != expected:
                raise RegistrationError(
                    [f"this deployment is connected to {expected}; select that repository"]
                )

    @app.get("/setup")
    def setup_page(request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        try:
            repo = connected_repo() if connected_repo else ""
        except ForgeError:
            return Response(status_code=303, headers={"Location": "/connect"})
        return Response(_render_form(storage, repo=repo), media_type="text/html")

    @app.post("/setup")
    async def setup_create(request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        fields = urllib.parse.parse_qs((await request.body()).decode("utf-8", errors="replace"))

        def value(key: str) -> str:
            return fields.get(key, [""])[0]

        try:
            check_repo(value("repo"))
            _service, key = register_service(
                storage,
                name=value("name"),
                repo=value("repo"),
                environment=value("environment") or "prod",
                webhook_secret=value("webhook_secret") or None,
            )
        except RegistrationError as exc:
            return Response(_render_form(storage, problems=exc.problems), media_type="text/html")
        return _key_page(_service.name, key, base_url)

    @app.post("/setup/rotate/{name}")
    async def setup_rotate(name: str, request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        rotated = rotate_ingest_key(storage, name)
        if rotated is None:
            return JSONResponse({"error": "unknown service"}, status_code=404)
        _service, key = rotated
        return _key_page(_service.name, key, base_url)

    @app.get("/api/services")
    def list_services(request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        return JSONResponse(
            {
                "services": [
                    {
                        "name": s.name,
                        "repo": s.repo,
                        "environment": s.environment,
                        "path": s.path,
                    }
                    for s in storage.list_services()
                ]
            }
        )

    @app.post("/api/services")
    async def create_service(request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        data = await request.json()
        try:
            check_repo(str(data.get("repo", "")))
            service, key = register_service(
                storage,
                name=str(data.get("name", "")),
                repo=str(data.get("repo", "")),
                environment=str(data.get("environment", "prod")),
                path=str(data.get("path", ".")),
                webhook_secret=data.get("webhook_secret") or None,
            )
        except RegistrationError as exc:
            return JSONResponse({"errors": exc.problems}, status_code=400)
        return JSONResponse(
            {
                "service": {
                    "name": service.name,
                    "repo": service.repo,
                    "environment": service.environment,
                },
                "ingest_key": key,
            },
            status_code=201,
        )

    @app.post("/api/services/{name}/rotate")
    async def rotate_service_key(name: str, request: Request) -> Response:
        if (denied := admin_guard(request)) is not None:
            return denied
        rotated = rotate_ingest_key(storage, name)
        if rotated is None:
            return JSONResponse({"error": "unknown service"}, status_code=404)
        return JSONResponse({"name": name, "ingest_key": rotated[1]})

    @app.patch("/api/services/{name}/config")
    async def update_service_config(name: str, request: Request) -> Response:
        """Update stored per-service config (autonomy dial, thresholds)."""
        if (denied := admin_guard(request)) is not None:
            return denied
        service = storage.get_service(name)
        if service is None:
            return JSONResponse({"error": "unknown service"}, status_code=404)
        data = await request.json()
        try:
            config = ServiceYaml.model_validate(data)
        except ValidationError as exc:
            return JSONResponse({"errors": [e["msg"] for e in exc.errors()]}, status_code=400)
        storage.save_service(service.model_copy(update={"config_json": config.model_dump_json()}))
        storage.record_audit(
            AuditEvent(
                actor="user:dashboard",
                action="service.config_update",
                subject=name,
                details={"autonomy": config.autonomy.value},
            )
        )
        return JSONResponse({"name": name, "config": config.model_dump()})
