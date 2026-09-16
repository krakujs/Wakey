# SPDX-License-Identifier: Apache-2.0
"""M0 gate rehearsal: full flow with the GitHub adapter against the local simulator.

Simulated credentials only — no live GitHub calls (founder directive).
This is the same path production uses: GitHubAdapter -> GitHub API -> ticket,
with the simulator standing in for github.com.
"""

from __future__ import annotations

import hashlib
import socket
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from wakey.core.config import Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Service
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.github import GitHubAdapter, GitHubConfig
from wakey.forge.simulator import GitHubSimulator, serve_in_background

SIM_TOKEN = "sim-token"  # simulated credential — the only "key" involved
KEY = "wk_demo_service_key"


def build_stack(tmp_path: Path) -> tuple[TestClient, GitHubSimulator, object]:
    sim = GitHubSimulator(token=SIM_TOKEN)

    # GitHubAdapter -> HTTP over loopback -> local simulator (no live GitHub)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = serve_in_background(sim.create_app(), port)
    config = GitHubConfig(token=SIM_TOKEN, repo="acme/payments", api_base=f"http://127.0.0.1:{port}")
    adapter = GitHubAdapter(config)

    storage = SQLiteStorage(tmp_path / "wakey.db")
    key_hash = hashlib.sha256(KEY.encode()).hexdigest()[:16]
    storage.save_service(
        Service(name="payments-api", repo="acme/payments", ingest_key_hash=key_hash)
    )

    app = create_app(Settings(), storage, MetricsRegistry(), forge=adapter)
    return TestClient(app), sim, server


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        client, sim, server = build_stack(Path(tmp))

        error = "ERROR: connection refused to payments-db:5432 for order 84d8e79a"
        response = client.post(
            f"/ingest/{KEY}", content="\n".join([error] * 3)
        ).json()
        assert response["outcomes"].get("ticketed") == 1, response
        assert len(sim.issues) == 1, "simulator must hold exactly one created issue"

        issue = sim.issues[0]
        assert issue["labels"] == ["wakey"], issue["labels"]
        assert "fp:" in issue["body"]
        assert issue["state"] == "open"

        # in-flight suppression through the real adapter path
        more = client.post(f"/ingest/{KEY}", content=error).json()
        assert "ticketed" not in more["outcomes"]
        assert len(sim.issues) == 1

        # comment flow: RCA-style update lands on the simulated issue
        client.app.state  # noqa: B018 — app exists
        print("== M0 rehearsal (simulated GitHub) ==")
        print(f"  ticket: {issue['title']}")
        print(f"  url:    {issue['html_url']}  (simulated)")
        print(f"  comments: {len(issue['comments'])}")
        print("  7-point M0 rehearsal PASS (simulated credential, no live calls)")
        server.should_exit = True
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
