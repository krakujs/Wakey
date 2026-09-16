# SPDX-License-Identifier: Apache-2.0
"""HTTP server bootstrap (E2-T4): health endpoints, metrics, request correlation.

The app is assembled via :func:`create_app` so tests can inject memory
implementations; ``python -m wakey serve`` wires the real system through
:mod:`wakey.core.composition` (R-01: one composition path — the ingest
route, durable delivery persistence, and worker runtime are always
present in production startup).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from wakey.core.logging import REQUEST_ID, new_request_id
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import AuditEvent, Delivery, DeployEvent, WorkState, utcnow
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort, TicketRef
from wakey.ingest.oidc import JwtVerifier, OidcVerificationError, decode_pubsub_envelope
from wakey.security.redaction import RedactionEngine
from wakey.security.secretbox import SecretBox
from wakey.web.auth import SESSION_COOKIE, AuthManager
from wakey.web.board import render_board_html
from wakey.web.commands import CommandDispatcher
from wakey.web.fingerprints import render_fingerprint_html
from wakey.web.fix_trigger import request_manual_fix
from wakey.web.github_setup import register_github_app_routes
from wakey.web.settings_page import render_settings_html
from wakey.web.setup import register_setup_routes

logger = logging.getLogger(__name__)

STARTED_AT = utcnow()

MAX_INGEST_BODY_BYTES = 1 * 1024 * 1024  # payload limit: reject before persisting


def create_app(
    settings: Any,  # Settings; typed loosely to keep the module import-light in tests
    storage: Storage,
    metrics: MetricsRegistry | None = None,
    *,
    forge: ForgePort | None = None,
    redactor: RedactionEngine | None = None,
    runtime: Any | None = None,  # composition.Runtime; starts workers via lifespan
    auth: AuthManager | None = None,
    commands: CommandDispatcher | None = None,
    secret_box: SecretBox | None = None,
) -> FastAPI:
    """Assemble the wakeyd HTTP app. One instance per process.

    The ingest route (E4-T1) exists only when a forge sink is provided —
    tests run ticket-less; production wires the real adapter (R-01).
    """
    metrics = metrics if metrics is not None else MetricsRegistry()
    metrics.inc("wakey_up", "1 if this wakeyd instance is running")
    engine = redactor if redactor is not None else RedactionEngine()

    lifespan = None
    if runtime is not None:

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            await runtime.start()
            try:
                yield
            finally:
                await runtime.stop()

    app = FastAPI(
        title="wakey",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.storage = storage
    app.state.settings = settings
    app.state.metrics = metrics
    app.state.redactor = engine

    @app.middleware("http")
    async def request_correlation(request: Any, call_next: Any) -> Any:
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        token = REQUEST_ID.set(request_id)
        try:
            response = await call_next(request)
        finally:
            REQUEST_ID.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

    auth_manager = auth if auth is not None else AuthManager(storage)
    app.state.auth = auth_manager
    secure_cookie = str(getattr(settings, "base_url", "")).startswith("https")

    def _admin_guard(request: Request) -> Response | None:
        """Redirect/401 when no valid admin session (R-07)."""
        if auth_manager.validate_session(request.cookies.get(SESSION_COOKIE)):
            return None
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "authentication required"}, status_code=401)
        return Response(status_code=302, headers={"Location": "/login"})

    register_auth_routes(app, auth_manager, metrics, secure_cookie=secure_cookie)

    register_core_routes(
        app,
        settings=settings,
        storage=storage,
        metrics=metrics,
        forge=forge,
        admin_guard=_admin_guard,
    )
    register_setup_routes(app, storage=storage, admin_guard=_admin_guard)
    register_github_app_routes(
        app,
        settings=settings,
        storage=storage,
        admin_guard=_admin_guard,
        secret_box=secret_box,
    )

    if forge is not None:
        register_ingest(app, storage, engine, metrics, forge, settings=settings)
    if commands is not None and forge is not None:
        register_webhooks(
            app,
            settings=settings,
            storage=storage,
            metrics=metrics,
            forge=forge,
            commands=commands,
        )
    return app


def register_ingest(
    app: FastAPI,
    storage: Storage,
    engine: RedactionEngine,
    metrics: MetricsRegistry,
    forge: ForgePort,
    *,
    settings: Any,
) -> None:
    """Wire POST /ingest/{service_key}: auth → redact → durable delivery → 202.

    The request path never parses or dispatches (R-02): the delivery is
    persisted atomically as ``pending`` and the durable worker owns the
    rest, so a forge outage or crash can neither lose accepted events nor
    hang the sender.
    """

    @app.post("/ingest/gcp/{service_key}")
    async def ingest_gcp(service_key: str, request: Request) -> Response:
        """GCP Cloud Logging via Pub/Sub push (WF-02 §3, E4-T4).

        Auth is layered: the service key (same as /ingest) plus — when
        ``WAKEY_OIDC_AUDIENCE`` is configured — verification of the push's
        OIDC Bearer token (issuer/audience/expiry/service account). The
        Pub/Sub messageId is the delivery id, so push retries deduplicate.
        """
        key_hash = hashlib.sha256(service_key.encode()).hexdigest()[:16]
        service = storage.get_service_by_ingest_key_hash(key_hash)
        if service is None:
            metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "auth"})
            return JSONResponse({"error": "unknown service key"}, status_code=401)

        audience = str(getattr(settings, "oidc_audience", ""))
        if audience:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.removeprefix("Bearer ").strip()
            verifier = JwtVerifier(
                audience=audience,
                authorized_service_account=str(getattr(settings, "oidc_service_account", "") or "")
                or None,
            )
            try:
                verifier.verify(token)
            except OidcVerificationError as exc:
                metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "oidc"})
                return JSONResponse({"error": f"OIDC verification failed: {exc}"}, status_code=401)

        body_bytes = await request.body()
        if len(body_bytes) > MAX_INGEST_BODY_BYTES:
            metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "size"})
            return JSONResponse(
                {"error": f"payload exceeds {MAX_INGEST_BODY_BYTES} bytes"}, status_code=413
            )
        try:
            payload, message_id = decode_pubsub_envelope(body_bytes)
        except ValueError as exc:
            metrics.inc("wakey_dead_letters_total", "dead-lettered lines", {"reason": "envelope"})
            return JSONResponse({"error": str(exc)}, status_code=400)

        redacted_payload, _ = engine.redact(payload)
        delivery = Delivery(
            service=service.name,
            delivery_id=f"gcp-{message_id}",
            payload=redacted_payload,
        )
        if not storage.save_delivery(delivery):
            metrics.inc("wakey_ingest_deduplicated_total", "replayed deliveries deduplicated")
            return JSONResponse({"deduplicated": True})
        metrics.inc("wakey_ingest_accepted_total", "deliveries accepted")
        return JSONResponse(
            {"accepted": True, "delivery_id": delivery.delivery_id, "state": "pending"},
            status_code=202,
        )

    @app.post("/ingest/{service_key}")
    async def ingest(service_key: str, request: Request) -> Response:
        key_hash = hashlib.sha256(service_key.encode()).hexdigest()[:16]
        service = storage.get_service_by_ingest_key_hash(key_hash)
        if service is None:
            metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "auth"})
            return JSONResponse({"error": "unknown service key"}, status_code=401)

        body_bytes = await request.body()
        if len(body_bytes) > MAX_INGEST_BODY_BYTES:
            metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "size"})
            return JSONResponse(
                {"error": f"payload exceeds {MAX_INGEST_BODY_BYTES} bytes"}, status_code=413
            )
        if service.webhook_secret:
            signature = request.headers.get("X-Wakey-Signature", "")
            expected = hmac.new(
                service.webhook_secret.encode(), body_bytes, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(f"sha256={expected}", signature):
                metrics.inc(
                    "wakey_ingest_rejected_total", "ingest rejections", {"reason": "signature"}
                )
                return JSONResponse({"error": "invalid signature"}, status_code=401)

        delivery_id = request.headers.get("X-Delivery-Id") or new_request_id()
        # Pass 1 redaction on the whole payload: multiline-secret capable,
        # before anything durable is written (R-04).
        redacted_body, _ = engine.redact(body_bytes.decode("utf-8", errors="replace"))
        delivery = Delivery(service=service.name, delivery_id=delivery_id, payload=redacted_body)
        if not storage.save_delivery(delivery):
            # replayed delivery: already handled or queued — never reprocess
            metrics.inc("wakey_ingest_deduplicated_total", "replayed deliveries deduplicated")
            return JSONResponse({"deduplicated": True})

        metrics.inc("wakey_ingest_accepted_total", "deliveries accepted")
        return JSONResponse(
            {"accepted": True, "delivery_id": delivery_id, "state": "pending"}, status_code=202
        )


_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wakey — sign in</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; background: #0b0d12; color: #a3a8b3;
    font: 14px/1.5 'Inter', ui-sans-serif, system-ui, sans-serif;
    display: flex; min-height: 100vh; align-items: center; justify-content: center;
  }
  .card {
    background: #161920; border-radius: 12px; padding: 32px; width: 360px;
  }
  h1 { color: #ffffff; font-size: 20px; font-weight: 500; margin: 0 0 8px; }
  p { margin: 0 0 20px; color: #7d828d; font-size: 13px; }
  input {
    width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #23262e;
    background: #0b0d12; color: #ffffff; font-size: 14px; letter-spacing: 2px;
  }
  button {
    margin-top: 12px; width: 100%; padding: 10px 12px; border: none;
    border-radius: 8px; background: #ffb020; color: #0b0d12;
    font-weight: 600; font-size: 14px; cursor: pointer;
  }
</style>
</head>
<body>
<main class="card">
<h1>wakey</h1>
<p>Paste the one-time setup token from the server console to start an admin
session. It is single-use and expires after 30 minutes.</p>
<form method="post" action="/login">
<input name="token" placeholder="WAKE-XXXX-XXXX-XXXX" autocomplete="off" required>
<button type="submit">Unlock wakey</button>
</form>
</main>
</body>
</html>
"""


def register_core_routes(
    app: FastAPI,
    *,
    settings: Any,
    storage: Storage,
    metrics: MetricsRegistry,
    forge: ForgePort | None,
    admin_guard: Callable[[Request], Response | None],
) -> None:
    """Health/readiness plus the authed settings/board pages (WF-11, WF-12)."""

    def _settings_summary() -> dict[str, object]:
        return {
            "port": settings.port,
            "base_url": settings.base_url,
            "database": "postgres" if settings.database_url else "sqlite",
            "llm_configured": bool(settings.llm_base_url and settings.llm_api_key),
            "llm_model": settings.llm_model or None,
            "log_level": settings.log_level,
        }

    @app.get("/")
    def root() -> Response:
        return Response(status_code=302, headers={"Location": "/board"})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Never checks dependencies (WF-11 §2)."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> Response:
        """Readiness: storage answers; honest about capability (R-01). Public."""
        return _readiness(storage, metrics, forge)

    @app.get("/api/settings")
    def settings_summary(request: Request) -> Response:
        """Non-secret settings view (WF-12 §7). Requires an admin session."""
        if (denied := admin_guard(request)) is not None:
            return denied
        return JSONResponse(_settings_summary())

    @app.get("/settings")
    def settings_page(request: Request) -> Response:
        """Settings page — requires an admin session (R-07)."""
        if (denied := admin_guard(request)) is not None:
            return denied
        return Response(render_settings_html(_settings_summary()), media_type="text/html")

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        """Public by design: aggregate counters only, no user data (WF-11 §5)."""
        return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/board")
    def board(request: Request) -> Response:
        """WF-12 page 3: the live board, admin-session required (R-07)."""
        if (denied := admin_guard(request)) is not None:
            return denied
        return Response(
            content=render_board_html(
                storage.list_active_fingerprints(), generated_at=utcnow().isoformat()
            ),
            media_type="text/html",
        )

    @app.get("/fingerprints/{fp_hash}")
    def fingerprint_detail(fp_hash: str, request: Request) -> Response:
        """WF-12 page 3b: one fingerprint's evidence trail, admin session required."""
        if (denied := admin_guard(request)) is not None:
            return denied
        fingerprint = storage.get_fingerprint(fp_hash)
        if fingerprint is None:
            return JSONResponse({"error": "unknown fingerprint"}, status_code=404)
        audits = storage.list_audit(subject=f"fp:{fp_hash}", limit=50)
        events = storage.recent_events(fingerprint.service, fingerprint.environment, limit=20)
        params = request.query_params
        return Response(
            content=render_fingerprint_html(
                fingerprint,
                events,
                audits,
                flash=params.get("flash"),
                flash_ok=params.get("flash_ok") == "1",
                generated_at=utcnow().isoformat(),
            ),
            media_type="text/html",
        )

    @app.post("/fingerprints/{fp_hash}/fix")
    def fix_request(fp_hash: str, request: Request) -> Response:
        """Manual fix trigger (WF-12 §8): gate + audit, then redirect with the outcome."""
        if (denied := admin_guard(request)) is not None:
            return denied
        origin = request.headers.get("origin")
        if origin is not None and origin not in _same_origins(request):
            metrics.inc("wakey_fix_csrf_blocked_total", "cross-origin fix requests rejected")
            return JSONResponse({"error": "cross-origin request rejected"}, status_code=403)
        outcome = request_manual_fix(storage, fp_hash)
        location = "/fingerprints/{}?flash={}&flash_ok={}".format(
            urllib.parse.quote(fp_hash, safe=""),
            urllib.parse.quote(outcome.message, safe=""),
            int(outcome.accepted),
        )
        return Response(status_code=303, headers={"Location": location})


def _same_origins(request: Request) -> set[str]:
    """Origin strings that match this request's own Host header (CSRF backstop)."""
    host = request.headers.get("host", "")
    return {f"http://{host}", f"https://{host}"}


def _readiness(storage: Storage, metrics: MetricsRegistry, forge: ForgePort | None) -> Response:
    """Ready when storage answers; honest about what the instance can do (R-01)."""
    if not storage.check_ready():
        metrics.inc("wakey_readiness_failures_total", "readyz probe failures")
        logger.warning("readiness probe failed: storage not reachable")
        return JSONResponse({"status": "not-ready", "storage": "unreachable"}, status_code=503)
    return JSONResponse(
        {
            "status": "ready",
            "services_configured": storage.has_services(),
            "ingest_enabled": forge is not None,
            "pending_deliveries": storage.pending_delivery_count(),
        }
    )


def register_auth_routes(
    app: FastAPI,
    auth_manager: AuthManager,
    metrics: MetricsRegistry,
    *,
    secure_cookie: bool,
) -> None:
    """Login/logout surface (WF-01 bootstrap admin, WF-12 §Auth, R-07).

    The setup token is single-use and 30-minutes bounded; a successful
    login starts a server-side session delivered as an HttpOnly,
    SameSite=Lax cookie (``Secure`` when BASE_URL is HTTPS). SameSite=Lax
    is the P1 CSRF control for the cookie-authenticated mutations.
    """

    @app.get("/login")
    def login_page() -> Response:
        return Response(_LOGIN_PAGE, media_type="text/html")

    @app.post("/login")
    async def login_submit(request: Request) -> Response:
        body = await request.body()
        fields = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
        token = fields.get("token", [""])[0]
        if not auth_manager.consume_setup_token(token):
            metrics.inc("wakey_login_failures_total", "failed dashboard logins")
            return JSONResponse({"error": "invalid or expired setup token"}, status_code=401)
        session_id = auth_manager.start_session()
        response: Response = Response(status_code=302, headers={"Location": "/board"})
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            httponly=True,
            samesite="lax",
            secure=secure_cookie,
            max_age=7 * 24 * 3600,
            path="/",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        auth_manager.revoke_session(request.cookies.get(SESSION_COOKIE))
        response = Response(status_code=302, headers={"Location": "/login"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response


def register_webhooks(
    app: FastAPI,
    *,
    settings: Any,
    storage: Storage,
    metrics: MetricsRegistry,
    forge: ForgePort,
    commands: CommandDispatcher,
) -> None:
    """Webhook surface (WF-07 §1, WF-08 §1): HMAC-verified, dedup, typed routing.

    Routes are intentionally public (GitHub/CI cannot hold a dashboard
    session); every request is HMAC-verified against WAKEY_WEBHOOK_SECRET
    and deduplicated by delivery id. Without a configured secret the
    surface refuses everything — fail closed.
    """

    def _hmac_ok(request: Request, body: bytes) -> bool:
        secret = getattr(settings, "webhook_secret", "")
        if not secret:
            return False  # fail closed: no secret configured, no acceptance
        signature = request.headers.get("X-Wakey-Signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> Response:
        body = await request.body()
        if not _hmac_ok(request, body):
            metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "signature"})
            return JSONResponse({"error": "invalid signature"}, status_code=401)

        delivery_id = request.headers.get("X-Delivery-Id") or new_request_id()
        delivery = Delivery(service="__webhook__", delivery_id=delivery_id, payload="")
        if not storage.save_delivery(delivery):
            return JSONResponse({"deduplicated": True})
        storage.complete_delivery("__webhook__", delivery_id)

        try:
            event = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        kind = request.headers.get("X-GitHub-Event", "")

        deps = _WebhookDeps(storage, metrics, forge, commands)
        return _route_github_event(request, event, kind, deps)

    @app.post("/webhooks/deploy")
    async def deploy_webhook(request: Request) -> Response:
        """Deploy-event ingestion (WF-02 §6, WF-08 deploy-aware verification)."""
        body = await request.body()
        if not _hmac_ok(request, body):
            metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "signature"})
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        service = storage.get_service(str(data.get("service", "")))
        if service is None:
            return JSONResponse({"error": "unknown service"}, status_code=404)
        storage.save_deploy(
            DeployEvent(
                service=service.name,
                environment=service.environment,
                sha=str(data.get("sha", "unknown")),
            )
        )
        storage.record_audit(
            AuditEvent(
                actor="webhook",
                action="deploy.record",
                subject=service.name,
                details={"sha": str(data.get("sha", "unknown"))},
            )
        )
        metrics.inc("wakey_deployments_total", "deploy events recorded")
        return JSONResponse({"recorded": True}, status_code=202)


@dataclass(frozen=True)
class _WebhookDeps:
    """Bundle of what webhook event handlers need (keeps signatures small)."""

    storage: Storage
    metrics: MetricsRegistry
    forge: ForgePort
    commands: CommandDispatcher


def _route_github_event(
    request: Request,
    event: dict[str, Any],
    kind: str,
    deps: _WebhookDeps,
) -> Response:
    """WF-07 §2-4 / WF-08 §1: typed dispatch of verified webhook events."""
    if kind == "ping":
        return JSONResponse({"ok": True})

    if kind == "issue_comment" and event.get("action") == "created":
        issue = event.get("issue", {})
        sender = str(event.get("sender", {}).get("login", ""))
        body_text = str(event.get("comment", {}).get("body", ""))
        issue_id = str(issue.get("number", ""))
        reply = deps.commands.handle_comment(sender, issue_id, body_text)
        if reply is not None:
            ref = TicketRef(issue_id=issue_id, url=str(issue.get("html_url", "")))
            deps.forge.add_comment(ref, reply)
        return JSONResponse({"handled": reply is not None})

    if kind == "pull_request" and event.get("action") == "closed":
        pull = event.get("pull_request", {})
        if not pull.get("merged"):
            return JSONResponse({"handled": False})
        head_ref = str(pull.get("head", {}).get("ref", ""))
        fingerprint = _start_verification(deps.storage, deps.metrics, head_ref)
        if fingerprint is None:
            return JSONResponse({"handled": False})
        if fingerprint.ticket_url:
            deps.forge.add_comment(
                TicketRef(
                    issue_id=fingerprint.ticket_issue_id or fingerprint.fp_hash,
                    url=fingerprint.ticket_url,
                ),
                f"Fix merged (`{str(pull.get('merge_commit_sha', ''))[:12]}`) — "
                "verification window armed. I will confirm or reopen.",
            )
        return JSONResponse({"handled": True, "state": "verifying"})

    return JSONResponse({"handled": False})


def _start_verification(
    storage: Storage, metrics: MetricsRegistry, proposal_branch: str
) -> Any | None:
    """WF-08 §1: bind a merged wakey PR → fingerprint enters ``verifying``."""
    fingerprint = storage.get_fingerprint_by_branch(proposal_branch)
    if fingerprint is None or fingerprint.state is WorkState.VERIFYING:
        return None
    now = utcnow()
    updated = fingerprint.model_copy(
        update={
            "state": WorkState.VERIFYING,
            "verification_started_at": now,
            "verification_start_occurrences": fingerprint.occurrences,
        }
    )
    storage.save_fingerprint(updated)
    storage.record_audit(
        AuditEvent(
            actor="webhook",
            action="verification.start",
            subject=f"fp:{fingerprint.fp_hash}",
            details={"branch": proposal_branch},
        )
    )
    metrics.inc("wakey_verifications_armed_total", "verification windows armed")
    return updated
