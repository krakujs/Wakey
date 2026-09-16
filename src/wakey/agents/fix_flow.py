# SPDX-License-Identifier: Apache-2.0
"""FixFlow (M2): eligibility gate -> repro-first fixer -> forge delivery.

Composes the three M2 pieces into one entry so callers (CLI, queue worker)
run a single method and get a final, auditable outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wakey.agents.fix import (
    EligibilityInput,
    ReproFirstFixer,
    check_eligibility,
)
from wakey.agents.fix_delivery import FixProposalDelivery
from wakey.core.models import Fingerprint


@dataclass(frozen=True)
class FlowResult:
    stage: str  # "delivered" | "refused" | "fix-failed"
    detail: str
    diff: str = ""


class FixFlow:
    def __init__(
        self,
        fixer: ReproFirstFixer,
        delivery: FixProposalDelivery,
    ) -> None:
        self._fixer = fixer
        self._delivery = delivery

    def run(
        self,
        workspace: Path,
        fingerprint: Fingerprint,
        rca_summary: str,
        inputs: EligibilityInput,
        test_command: list[str],
    ) -> FlowResult:
        gate = check_eligibility(inputs)
        if not gate.allowed:
            return FlowResult("refused", gate.reason)
        fix_result = self._fixer.execute(workspace, fingerprint, rca_summary, test_command)
        if not fix_result.ok:
            return FlowResult("fix-failed", fix_result.reason)
        delivered = self._delivery.deliver(fingerprint, fix_result, rca_summary)
        url = delivered.get("url", "") if isinstance(delivered, dict) else ""
        return FlowResult("delivered", f"proposal at {url}", fix_result.diff)
