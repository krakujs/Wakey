# SPDX-License-Identifier: Apache-2.0
"""Isolated fix-executor service (readiness #5): the only host that touches
a container runtime.

wakeyd ships a workspace tarball and a test argv; this service enforces
everything else server-side — the operator-provisioned image, the Docker
isolation caps (network-less, read-only, 512MB/1CPU/64-pid), a bounded
concurrency semaphore, upload size caps, and safe tar extraction. The
caller's authority is deliberately narrow: it cannot choose an image,
mounts, env, or capabilities. A shared bearer token gates the endpoint;
put real TLS and network policy in front of it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from wakey.agents.sandbox import DockerTestRunner, SandboxError
from wakey.agents.workspace import extract_tarball_bytes

logger = logging.getLogger(__name__)

MAX_RUN_BODY_BYTES = 40 * 1024 * 1024  # tarball + multipart overhead
MAX_ARGV_ITEMS = 16
MAX_ARGV_ITEM_CHARS = 512


@dataclass(frozen=True)
class _RunRequest:
    """A validated /v1/run payload."""

    argv: list[str]
    tarball: bytes


def _constant_time_token(expected: str, presented: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(expected.encode()).digest(), hashlib.sha256(presented.encode()).digest()
    )


def create_executor_app(
    runner: DockerTestRunner, token: str, *, max_concurrent: int = 2
) -> FastAPI:
    """Build the executor app around an already-configured test runner."""
    if not token:
        raise SystemExit("WAKEY_EXECUTOR_TOKEN must be set — the executor never runs open")
    slots = threading.BoundedSemaphore(max_concurrent)
    running = {"count": 0}

    app = FastAPI(title="wakey-executor", docs_url=None, redoc_url=None, openapi_url=None)

    def _authorized(request: Request) -> bool:
        header = request.headers.get("Authorization", "")
        return header.startswith("Bearer ") and _constant_time_token(
            token, header.removeprefix("Bearer ")
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ready": True, "running": running["count"]}

    @app.post("/v1/run")
    async def run(request: Request) -> Response:
        if not _authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=403)
        parsed = await _read_run_form(request)
        if not isinstance(parsed, _RunRequest):
            return parsed  # a validation Response
        argv_list = parsed.argv
        tarball = parsed.tarball

        import tempfile  # noqa: PLC0415 — thread-confined work dir
        from pathlib import Path  # noqa: PLC0415

        with tempfile.TemporaryDirectory(prefix="wakey-exec-") as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            try:
                extract_tarball_bytes(tarball, workspace)
            except Exception as exc:  # noqa: BLE001 — extraction gates reject loudly
                return JSONResponse({"error": f"unsafe workspace: {exc}"}, status_code=422)
            if not slots.acquire(blocking=False):
                return JSONResponse(
                    {"error": "executor busy; retry shortly"},
                    status_code=503,
                    headers={"Retry-After": "10"},
                )
            running["count"] += 1
            try:
                outcome = await asyncio.to_thread(runner, workspace, argv_list)
            except SandboxError as exc:
                logger.warning("sandbox infrastructure failure: %s", exc)
                return JSONResponse({"error": str(exc)}, status_code=503)
            finally:
                running["count"] -= 1
                slots.release()
            return JSONResponse({"ok": outcome.ok, "output": outcome.output})

    return app


async def _read_run_form(request: Request) -> _RunRequest | Response:
    """Validate the /v1/run form: tarball + bounded argv; a Response on rejection."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_RUN_BODY_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    form = await request.form()
    upload = form.get("workspace")
    raw_argv = form.get("argv")
    if not isinstance(upload, UploadFile) or not isinstance(raw_argv, str):
        return JSONResponse({"error": "workspace tarball and argv are required"}, status_code=422)
    try:
        argv_list = json.loads(raw_argv)
    except json.JSONDecodeError:
        return JSONResponse({"error": "argv must be a JSON list"}, status_code=422)
    if (
        not isinstance(argv_list, list)
        or not argv_list
        or len(argv_list) > MAX_ARGV_ITEMS
        or not all(isinstance(a, str) and 0 < len(a) <= MAX_ARGV_ITEM_CHARS for a in argv_list)
    ):
        return JSONResponse({"error": "argv must be 1-16 short strings"}, status_code=422)
    tarball = await upload.read()
    if len(tarball) > MAX_RUN_BODY_BYTES:
        return JSONResponse({"error": "workspace exceeds the size cap"}, status_code=413)
    return _RunRequest(argv=[str(a) for a in argv_list], tarball=tarball)


def main() -> None:
    parser = argparse.ArgumentParser(description="wakey isolated fix-executor service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8490)
    args = parser.parse_args()

    logging.basicConfig(level=os.environ.get("WAKEY_LOG_LEVEL", "INFO").upper())
    # The daemon is normally reached through a socket proxy in another
    # container. It cannot resolve executor-container paths, so transfer the
    # workspace through Docker's archive API instead of a bind mount.
    runner = DockerTestRunner(os.environ.get("WAKEY_SANDBOX_IMAGE", ""), workspace_mode="copy")
    app = create_executor_app(runner, os.environ.get("WAKEY_EXECUTOR_TOKEN", ""))
    import uvicorn  # noqa: PLC0415 — server-only dependency path

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
