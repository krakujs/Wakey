# SPDX-License-Identifier: Apache-2.0
"""M2 loop E2E (R-06/R-09): repro → tested patch → published branch/PR → verdict.

Runs the real GitHubAdapter over the local simulator (no live calls): the
fixer proves a patch on a seeded workspace, delivery publishes the tested
files to a real branch and opens a draft PR, and the verification watcher
applies a persisted verdict — closing the loop the way production would.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wakey.agents.fix import ReproFirstFixer
from wakey.agents.fix_delivery import FixProposalDelivery
from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.forge.github import GitHubAdapter, GitHubConfig
from wakey.forge.simulator import GitHubSimulator, serve_in_background
from wakey.policy.verification import Verdict, WatchInputs
from wakey.policy.watcher import LABEL_VERIFIED, VerificationWatcher

SEEDED_BUG = 'def get_idempotency_key(order):\n    return order["idempotency_key"]\n'
SCRIPTED_PATCH = 'def get_idempotency_key(order):\n    return order.get("idempotency_key")\n'
REPRO = (
    "from billing import get_idempotency_key\n"
    "\n\n"
    "def test_missing_key_returns_none():\n"
    "    assert get_idempotency_key({}) is None\n"
)
RCA_SUMMARY = "KeyError for legacy orders missing the idempotency key"


class ScriptedProposer:
    def repro_test(self, fingerprint: Fingerprint, files: dict[str, str]) -> tuple[str, str]:
        return "tests/test_repro.py", REPRO

    def patch(
        self,
        fingerprint: Fingerprint,
        rca_summary: str,
        files: dict[str, str],
        last_failure: str = "",
    ) -> dict[str, str]:
        return {"billing.py": SCRIPTED_PATCH}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_fingerprint() -> Fingerprint:
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="KeyError: 'idempotency_key'",
        severity=Severity.ERROR,
        occurrences=5,
        ticket_issue_id="1",
        ticket_url="https://github.sim/acme/payments/issues/1",
    )


def _verification_phase(
    storage: SQLiteStorage,
    adapter: GitHubAdapter,
    fingerprint: Fingerprint,
    sim_issues: list[dict[str, object]],
    issue_id: str,
) -> None:
    """Ship the fix, elapse the grace window quietly, verify — persisted."""
    started = datetime.now(UTC) - timedelta(minutes=45)
    verifying = storage.get_fingerprint(fingerprint.fp_hash)
    assert verifying is not None
    verifying = verifying.model_copy(
        update={
            "state": WorkState.VERIFYING,
            "verification_started_at": started,
            "verification_start_occurrences": 5,
            "occurrences": 5,
        }
    )
    storage.save_fingerprint(verifying)

    def grace_inputs(fp: Fingerprint) -> WatchInputs:
        fresh = storage.get_fingerprint(fp.fp_hash)
        current = fresh.occurrences if fresh else fp.occurrences
        anchor = fresh.verification_start_occurrences if fresh else 0
        begin = fresh.verification_started_at if fresh else None
        assert begin is not None
        elapsed = (datetime.now(UTC) - begin).total_seconds() >= 30 * 60
        return WatchInputs(
            occurrences_after_fix=current - anchor,
            grace_window_elapsed=elapsed,
            fix_deployed_to_environment=True,
        )

    watcher = VerificationWatcher(storage, adapter, grace_inputs=grace_inputs)
    results = watcher.run_once()
    assert [r.verdict for r in results] == [Verdict.SUCCESS]

    sim_issue = next(i for i in sim_issues if i["number"] == int(issue_id))
    assert sim_issue["state"] == "closed"
    assert LABEL_VERIFIED in sim_issue["labels"]
    final = storage.get_fingerprint(fingerprint.fp_hash)
    assert final is not None and final.state is WorkState.VERIFIED_CLOSED


@pytest.mark.timeout(60)
def test_m2_loop_publishes_and_verifies(tmp_path: Path) -> None:
    sim = GitHubSimulator(token="sim-token")
    port = _free_port()
    server = serve_in_background(sim.create_app(), port)
    try:
        config = GitHubConfig(
            token="sim-token",
            repo="acme/payments",
            api_base=f"http://127.0.0.1:{port}",
        )
        adapter = GitHubAdapter(config)

        # an issue representing the ticket exists on the forge
        storage = SQLiteStorage(tmp_path / "wakey.db")
        fingerprint = make_fingerprint()
        storage.save_fingerprint(fingerprint)
        issue = adapter.create_ticket(fingerprint, "[wakey] seeded bug", "body")
        stored = storage.get_fingerprint(fingerprint.fp_hash)
        assert stored is not None
        stored = stored.model_copy(
            update={"ticket_issue_id": issue.issue_id, "ticket_url": issue.url}
        )
        storage.save_fingerprint(stored)

        # repro-first fix on a seeded workspace
        workspace = tmp_path / "repo"
        workspace.mkdir()
        (workspace / "billing.py").write_text(SEEDED_BUG)
        fix_result = ReproFirstFixer(ScriptedProposer()).execute(
            workspace,
            fingerprint,
            RCA_SUMMARY,
            [sys.executable, "-m", "pytest", "-q", str(workspace)],
        )
        assert fix_result.ok, fix_result.reason

        # delivery publishes the tested tree, not just a diff in the body
        delivery = FixProposalDelivery(storage, adapter)
        outcome = delivery.deliver(stored, fix_result, RCA_SUMMARY)
        assert outcome["delivered"] is True
        branch = str(outcome["branch"])

        published = sim.branch_files("acme/payments", branch)
        assert published["billing.py"] == SCRIPTED_PATCH, "committed tree must be the tested tree"
        assert "tests/test_repro.py" in published, "repro is published with the fix"
        assert len(sim.pulls) == 1
        assert sim.pulls[0]["head"] == branch
        assert sim.pulls[0]["base"] == "main"
        assert sim.pulls[0]["draft"] is True

        # verification: fix ships, window elapses quietly -> verdict persisted
        _verification_phase(storage, adapter, fingerprint, sim.issues, issue.issue_id)

        reopened_storage = SQLiteStorage(tmp_path / "wakey.db")
        reread = reopened_storage.get_fingerprint(fingerprint.fp_hash)
        assert reread is not None and reread.state is WorkState.VERIFIED_CLOSED
        reopened_storage.close()
    finally:
        server.should_exit = True
        deadline = time.monotonic() + 5
        while server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        for thread in threading.enumerate():
            if thread.daemon and thread.name.startswith("uvicorn"):
                pass  # daemon threads die with the process; nothing to join
