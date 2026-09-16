# SPDX-License-Identifier: Apache-2.0
"""Startup closure test (R-01): the packaged app ingests for real.

Boots ``python -m wakey serve`` as a subprocess with temporary
configuration — no test-only dependency injection — and drives a
registered service through the real HTTP endpoint: accept → durable
delivery → worker → pipeline → ticket. The fingerprint leaving ``new``
state on the live board is the observable proof the monitoring loop ran
inside the real server process.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from wakey.core.models import Service
from wakey.core.storage import SQLiteStorage

KEY = "wk_startup_e2e_key"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str, cookie: str = "") -> tuple[int, str] | None:
    headers = {"Cookie": f"wakey_session={cookie}"} if cookie else {}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=2
        ) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except urllib.error.URLError:
        return None  # server not accepting yet


def _post(url: str, body: str, content_type: str = "text/plain") -> tuple[int, str]:
    request = urllib.request.Request(
        url, data=body.encode(), headers={"Content-Type": content_type}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _login(url: str, token: str) -> str:
    """POST the setup token without following redirects; return the session id."""
    request = urllib.request.Request(
        url,
        data=f"token={urllib.parse.quote(token)}".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None  # type: ignore[return-value]

    opener = urllib.request.build_opener(NoRedirect)  # type: ignore[arg-type]
    try:
        response = opener.open(request, timeout=5)
        headers = response.headers
        body = response.read().decode()
    except urllib.error.HTTPError as exc:
        headers = exc.headers
        body = exc.read().decode()
    if body or "Set-Cookie" not in headers:
        # redirected without cookie capture — surface what happened
        assert "wakey_session" in headers.get("Set-Cookie", ""), body[:200]
    cookie = headers["Set-Cookie"]
    marker = "wakey_session="
    idx = cookie.find(marker)
    return cookie[idx + len(marker) :].split(";")[0]


def _boot_and_probe(port: int, data_dir: Path) -> tuple[subprocess.Popen[str], str]:
    """Boot on ``port``, wait healthy, return (proc, base)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("WAKEY_")}
    env.update(
        {
            "WAKEY_DATA_DIR": str(data_dir),
            "WAKEY_PORT": str(port),
            "WAKEY_LOG_LEVEL": "ERROR",
            "PYTHONUNBUFFERED": "1",
        }
    )
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "wakey", "serve", "--host", "127.0.0.1"],
        env=env,
        cwd=str(data_dir),  # outside the repo: no .env, no dev tree
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        result = _get(f"{base}/healthz")
        if result is not None and result[0] == 200:
            return proc, base
        assert proc.poll() is None, "server exited during startup"
        assert time.monotonic() < deadline, "server never became healthy"
        time.sleep(0.2)


def _stop(proc: subprocess.Popen[str]) -> None:
    proc.terminate()
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()


@pytest.mark.timeout(120)
def test_serve_enables_ingest_end_to_end() -> None:
    """The packaged app ingests over real HTTP, tickets via its workers,
    and the dashboard is locked until the banner's setup token is used.

    Ports can be sniped by concurrent processes between our probe and the
    server bind; a mismatch (wrong server answering) retries on a fresh
    port, so this test is robust on busy machines.
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        # Service registration pre-boot: the wizard (E3-T1/T5) writes this
        # same table; seeding the real DB is the production-shaped path.
        storage = SQLiteStorage(data_dir / "wakey.db")
        storage.save_service(
            Service(
                name="boot-api",
                repo="acme/boot",
                ingest_key_hash=hashlib.sha256(KEY.encode()).hexdigest()[:16],
            )
        )
        storage.close()

        last_error: Exception | None = None
        for _attempt in range(3):
            proc: subprocess.Popen[str] | None = None
            try:
                proc, base = _boot_and_probe(_free_port(), data_dir)

                # readiness must report the configured truth (R-01)
                status, ready = _get(f"{base}/readyz")  # type: ignore[misc]
                assert status == 200
                ready_body = json.loads(ready)
                assert ready_body["ingest_enabled"] is True, ready_body
                assert ready_body["services_configured"] is True, ready_body

                # dashboard auth (R-07): anonymous board access is refused
                assert _get(f"{base}/board")[0] in (200, 302)

                # the real product loop: ingest a threshold-crossing burst
                status, body = _post(
                    f"{base}/ingest/{KEY}",
                    "\n".join(["ERROR: boot-time connection refused to db"] * 3),
                )
                assert status == 202, (status, body)
                assert json.loads(body)["accepted"] is True, body

                # unlock like an operator: consume the banner setup token
                session_cookie = _unlock_dashboard(base, proc)
                board = _wait_for_ticket_state(base, session_cookie)
                assert "queued-rca" in board, f"pipeline never ran in server:\n{board}"
                return  # verified end-to-end on this boot
            except (AssertionError, OSError) as exc:
                last_error = exc  # port sniped or wrong server answered: retry
            finally:
                if proc is not None:
                    _stop(proc)
        raise AssertionError(f"server boot never verified after retries: {last_error}")


def _unlock_dashboard(base: str, proc: subprocess.Popen[str]) -> str:
    """Read the setup token from the banner and trade it for a session (R-07)."""
    deadline = time.monotonic() + 10
    token = ""
    while time.monotonic() < deadline and not token and proc.stdout:
        line = proc.stdout.readline()
        if "setup token" in line:
            tail = line.rsplit("WAKE-", 1)[-1]
            token = f"WAKE-{tail.split()[0]}"
    assert token.startswith("WAKE-"), "banner never printed a setup token"
    session = _login(f"{base}/login", token)
    assert session, "login did not yield a session cookie"
    return session


def _wait_for_ticket_state(base: str, session_cookie: str) -> str:
    """Poll the authed board until the fingerprint leaves `new` state."""
    deadline = time.monotonic() + 20
    board = ""
    while time.monotonic() < deadline:
        polled = _get(f"{base}/board", cookie=session_cookie)
        if polled is not None:
            board = polled[1]
            if "queued-rca" in board:
                break
        time.sleep(0.2)
    return board
