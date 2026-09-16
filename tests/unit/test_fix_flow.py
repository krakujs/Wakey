# SPDX-License-Identifier: Apache-2.0
"""FixFlow end-to-end test: gate -> fixer -> delivery."""

from __future__ import annotations

import sys
from pathlib import Path

from wakey.agents.fix import EligibilityInput, ReproFirstFixer
from wakey.agents.fix_delivery import FixProposalDelivery
from wakey.agents.fix_flow import FixFlow
from wakey.core.config import Autonomy
from wakey.core.models import Fingerprint, Severity
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge

SEEDED_BUG = 'def get_idempotency_key(order):\n    return order["idempotency_key"]\n'
SCRIPTED_PATCH = 'def get_idempotency_key(order):\n    return order.get("idempotency_key")\n'
REPRO = (
    "from billing import get_idempotency_key\n"
    "\n"
    "\n"
    "def test_missing_key_returns_none():\n"
    "    assert get_idempotency_key({}) is None\n"
)


class ScriptedProposer:
    def repro_test(self, fingerprint: Fingerprint, files: dict[str, str]) -> tuple[str, str]:
        return "tests/test_repro.py", REPRO

    def patch(
        self, fingerprint: Fingerprint, rca_summary: str, files: dict[str, str]
    ) -> dict[str, str]:
        return {"billing.py": SCRIPTED_PATCH}


def test_fix_flow_delivers_end_to_end(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "billing.py").write_text(SEEDED_BUG)

    fixer = ReproFirstFixer(ScriptedProposer())
    delivery = FixProposalDelivery(storage, forge)
    flow = FixFlow(fixer, delivery)

    fingerprint = Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="KeyError: 'idempotency_key'",
        severity=Severity.ERROR,
        occurrences=5,
    )
    inputs = EligibilityInput(
        rca_class="code-fix",
        confidence=0.9,
        autonomy=Autonomy.FIX,
        confidence_floor=0.75,
        open_prs=0,
        max_open_prs=3,
    )
    result = flow.run(
        workspace,
        fingerprint,
        "KeyError for legacy orders",
        inputs,
        [sys.executable, "-m", "pytest", "-q", str(workspace)],
    )

    assert result.stage == "delivered"
    assert "billing.py" in result.diff
    assert len(forge.proposals) == 1
    assert forge.proposals[0]["title"].startswith("[wakey] Fix for fp:")


def test_fix_flow_refuses_below_floor(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    flow = FixFlow(ReproFirstFixer(ScriptedProposer()), FixProposalDelivery(storage, forge))
    fingerprint = Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="KeyError",
        severity=Severity.ERROR,
        occurrences=3,
    )
    inputs = EligibilityInput(
        rca_class="code-fix",
        confidence=0.5,
        autonomy=Autonomy.FIX,
        confidence_floor=0.75,
        open_prs=0,
        max_open_prs=3,
    )
    result = flow.run(tmp_path, fingerprint, "weak evidence", inputs, [sys.executable, "-c", "1"])
    assert result.stage == "refused"
    assert "confidence" in result.detail
