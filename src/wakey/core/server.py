# SPDX-License-Identifier: Apache-2.0
"""HTTP server bootstrap (E2-T4): health endpoints, metrics, request correlation.

The app is assembled via :func:`create_app` so tests can inject memory
implementations; ``python -m wakey serve`` (see ``__main__.py``) wires the
real SQLite storage and uvicorn.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from wakey.core.logging import REQUEST_ID, new_request_id
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import utcnow
from wakey.core.storage import Storage

logger = logging.getLogger(__name__)

STARTED_AT = utcnow()


def create_app(
    settings: Any,  # Settings; typed loosely to keep the module import-light in tests
    storage: Storage,
    metrics: MetricsRegistry | None = None,
) -> FastAPI:
    """Assemble the wakeyd HTTP app. One instance per process."""
    metrics = metrics if metrics is not None else MetricsRegistry()
    metrics.inc("wakey_up", "1 if this wakeyd instance is running")

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

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")

    return app
