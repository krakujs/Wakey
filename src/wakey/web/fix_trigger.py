# SPDX-License-Identifier: Apache-2.0
"""Manual fix trigger from the dashboard (WF-12 §8, founder directive 2026-09-16).

A human clicking "request fix" gets exactly what the autonomous loop gets:
the FIX-1 eligibility gate evaluated against live state, a named outcome,
and an audit line. The gate never half-applies — either every condition
holds (and the fix executor runs) or the refusal names the failed check.

P1 executor scope: patch generation (model proposer, E8-T5) and the
capped-container sandbox gate (E8-T4) are not landed, so an eligible
request is honestly blocked at the executor stage with those task IDs —
the same convention the ``@wakey fix`` command uses (WF-07).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from wakey.agents.fix import EligibilityInput, check_eligibility
from wakey.agents.rca import RcaResult, classify
from wakey.core.config import ServiceYaml
from wakey.core.models import AuditEvent, Fingerprint, WorkState
from wakey.core.storage import Storage

EXECUTOR_NOTE = (
    "Eligibility passed, but fix execution is not enabled on this instance yet: "
    "the patch proposer is not built (E8-T5) and the sandbox container gate is "
    "open (E8-T4). No state was changed."
)


@dataclass(frozen=True)
class FixRequestOutcome:
    """Result surfaced to the dashboard flash and the audit log."""

    accepted: bool
    message: str


def latest_rca(storage: Storage, fp_hash: str) -> RcaResult | None:
    """(class, confidence, summary) from the newest ``rca.classify`` audit line."""
    for event in storage.list_audit(subject=f"fp:{fp_hash}", limit=50):
        if event.action != "rca.classify":
            continue
        details = event.details
        try:
            confidence = float(details.get("confidence", "0"))
        except ValueError:
            confidence = 0.0
        return RcaResult(
            details.get("class", "needs-human"), confidence, details.get("summary", "")
        )
    return None


def service_config(storage: Storage, service: str) -> ServiceYaml:
    stored = storage.get_service(service)
    if stored is None:
        return ServiceYaml()
    try:
        return ServiceYaml.model_validate_json(stored.config_json)
    except ValidationError:
        return ServiceYaml()


def request_manual_fix(
    storage: Storage, fp_hash: str, *, actor: str = "user:dashboard"
) -> FixRequestOutcome:
    """Evaluate one human-requested fix attempt; audit and return the outcome."""
    fingerprint = storage.get_fingerprint(fp_hash)
    if fingerprint is None:
        return FixRequestOutcome(False, f"unknown fingerprint {fp_hash}")

    rca = latest_rca(storage, fp_hash) or classify(fingerprint)
    rca_class, confidence, _summary = rca.classification, rca.confidence, rca.summary
    config = service_config(storage, fingerprint.service)
    open_prs = 1 if _proposal_in_flight(fingerprint) else 0

    gate = check_eligibility(
        EligibilityInput(
            rca_class=rca_class,
            confidence=confidence,
            autonomy=config.autonomy,
            confidence_floor=config.confidence_floor,
            open_prs=open_prs,
            max_open_prs=config.max_open_prs,
            chronic=fingerprint.chronic,
        )
    )
    if not gate.allowed:
        _audit(storage, actor, fp_hash, f"refused: {gate.reason}")
        return FixRequestOutcome(False, gate.reason)

    _audit(storage, actor, fp_hash, "blocked-executor")
    return FixRequestOutcome(False, EXECUTOR_NOTE)


def _proposal_in_flight(fingerprint: Fingerprint) -> bool:
    return bool(fingerprint.proposal_issue_id) and fingerprint.state in (
        WorkState.FIXING,
        WorkState.VERIFYING,
    )


def _audit(storage: Storage, actor: str, fp_hash: str, result: str) -> None:
    storage.record_audit(
        AuditEvent(
            actor=actor,
            action="fix.requested",
            subject=f"fp:{fp_hash}",
            details={"result": result},
        )
    )
