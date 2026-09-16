# SPDX-License-Identifier: Apache-2.0
"""HTTP server bootstrap (E2-T4): health endpoints, metrics, request correlation.

The app is assembled via :func:`create_app` so tests can inject memory
implementations; ``python -m wakey serve`` (see ``__main__.py``) wires the
real SQLite storage and uvicorn.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from wakey.core.logging import REQUEST_ID, new_request_id
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import utcnow
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort
from wakey.ingest.normalizers import EventContext, parse_events
from wakey.ingest.pipeline import IngestPipeline
from wakey.security.redaction import RedactionEngine
from wakey.web.board import render_board_html

logger = logging.getLogger(__name__)

STARTED_AT = utcnow()


def create_app(
    settings: Any,  # Settings; typed loosely to keep the module import-light in tests
    storage: Storage,
    metrics: MetricsRegistry | None = None,
    forge: ForgePort | None = None,
    redactor: RedactionEngine | None = None,
) -> FastAPI:
    """Assemble the wakeyd HTTP app. One instance per process.

    The ingest route (E4-T1) exists only when a forge sink is provided —
    tests run ticket-less; production wires the real adapter.
    """
    metrics = metrics if metrics is not None else MetricsRegistry()
    metrics.inc("wakey_up", "1 if this wakeyd instance is running")
    pipeline = IngestPipeline(storage, forge) if forge is not None else None
    engine = redactor if redactor is not None else RedactionEngine()

    app = FastAPI(title="wakey", version="0.1.0.dev0", docs_url=None, redoc_url=None)
    app.state.storage = storage
    app.state.settings = settings
    app.state.metrics = metrics

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

    @app.get("/")
    def root() -> Response:
        return Response(status_code=302, headers={"Location": "/board"})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Never checks dependencies (WF-11 §2)."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> Response:
        """Readiness: dependencies answer (storage now; forge/LLM probes later)."""
        if storage.check_ready():
            return JSONResponse({"status": "ready"})
        metrics.inc("wakey_readiness_failures_total", "readyz probe failures")
        logger.warning("readiness probe failed: storage not reachable")
        return JSONResponse({"status": "not-ready", "storage": "unreachable"}, status_code=503)

    @app.get("/api/settings")
    def settings_summary() -> dict[str, object]:
        """Non-secret settings view for the settings page (WF-12 §7)."""
        return {
            "port": settings.port,
            "base_url": settings.base_url,
            "database": "postgres" if settings.database_url else "sqlite",
            "llm_configured": bool(settings.llm_base_url and settings.llm_api_key),
            "llm_model": settings.llm_model or None,
            "log_level": settings.log_level,
        }

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/board")
    def board() -> Response:
        """WF-12 page 3: the live board, server-rendered (NFR-9: no JS frameworks)."""
        return Response(
            content=render_board_html(
                storage.list_active_fingerprints(),
                generated_at=utcnow().isoformat(),
            ),
            media_type="text/html",
        )

    if pipeline is not None:
        register_ingest(app, storage, pipeline, engine, metrics)

    return app


def register_ingest(
    app: FastAPI,
    storage: Storage,
    pipeline: IngestPipeline,
    engine: RedactionEngine,
    metrics: MetricsRegistry,
) -> None:
    """Wire POST /ingest/{service_key}: auth -> parse -> redact -> pipeline."""

    @app.post("/ingest/{service_key}")
    async def ingest(service_key: str, request: Request) -> Response:
        key_hash = hashlib.sha256(service_key.encode()).hexdigest()[:16]
        service = storage.get_service_by_ingest_key_hash(key_hash)
        if service is None:
            metrics.inc("wakey_ingest_rejected_total", "ingest rejections", {"reason": "auth"})
            return JSONResponse({"error": "unknown service key"}, status_code=401)

        body_bytes = await request.body()
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
        if not storage.record_delivery(delivery_id):
            return JSONResponse({"deduplicated": True})  # replayed delivery (WF-02 §7)

        body = body_bytes.decode("utf-8", errors="replace")
        parsed = parse_events(
            body,
            EventContext(
                service=service.name,
                environment=service.environment,
                source="ingest",
                delivery_id=delivery_id,
            ),
        )
        outcomes: dict[str, int] = {}
        for event in parsed.events:
            redacted_message, _ = engine.redact(event.message)
            redacted_attrs = {k: engine.redact(v)[0] for k, v in event.attributes.items()}
            clean = event.model_copy(
                update={"message": redacted_message, "attributes": redacted_attrs}
            )
            storage.save_log_event(clean)
            result = pipeline.handle_event(clean)
            outcomes[result.outcome.value] = outcomes.get(result.outcome.value, 0) + 1
        for _, reason in parsed.dead_letters:
            metrics.inc(
                "wakey_dead_letters_total", "dead-lettered lines", {"reason": reason.split(":")[0]}
            )
        metrics.inc("wakey_ingest_accepted_total", "events accepted")
        return JSONResponse(
            {
                "accepted": len(parsed.events),
                "dead_lettered": len(parsed.dead_letters),
                "outcomes": outcomes,
            }
        )
