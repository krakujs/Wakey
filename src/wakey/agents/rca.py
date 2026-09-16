# SPDX-License-Identifier: Apache-2.0
"""RCA agent skeleton (WF-05, M1): classify a fingerprint and explain it.

M0+ slice: a **heuristic classifier** (deterministic, no keys) covering the
legacy staples — dependency/refusal errors, missing modules, permission
problems, and code-fix candidates with stack frames. The model tier refines
these heuristics when a live profile is configured; without one, Wakey
still triages (graceful degradation, NFR-4).
"""

from __future__ import annotations

from dataclasses import dataclass

from wakey.core.models import AuditEvent, Fingerprint
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort, TicketRef

_CODE_FIX_MARKERS = (
    "typeerror",
    "keyerror",
    "attributeerror",
    "valueerror",
    "indexerror",
    "undefined",
)
_INFRA_MARKERS = (
    "connection refused",
    "timed out",
    "timeout",
    "econnrefused",
    "connection reset",
    "permission denied",
    "address already in use",
    "disk quota",
)
_CONFIG_MARKERS = ("no module named", "modulenotfounderror", "importerror", "cannot find module")


@dataclass(frozen=True)
class RcaResult:
    classification: str  # code-fix | config | infra | needs-human
    confidence: float
    summary: str


def classify(fingerprint: Fingerprint) -> RcaResult:
    """Heuristic triage from fingerprint evidence (model refines when wired)."""
    haystack = fingerprint.template.lower()

    if any(marker in haystack for marker in _INFRA_MARKERS):
        return RcaResult(
            "infra", 0.8, "Dependency or environment failure (refusal/timeout pattern)."
        )
    if any(marker in haystack for marker in _CONFIG_MARKERS):
        return RcaResult("config", 0.7, "Missing module or import — likely packaging/deploy drift.")
    if any(marker in haystack for marker in _CODE_FIX_MARKERS) and fingerprint.frames:
        return RcaResult(
            "code-fix", 0.6, "Runtime type/access error with stack frames — code path identified."
        )
    if fingerprint.frames:
        return RcaResult("needs-human", 0.4, "Error with stack context but no recognised pattern.")
    return RcaResult("needs-human", 0.2, "No stack frames and no known pattern — needs human eyes.")


class RcaAgent:
    """Investigates a fingerprint and posts the RCA as a forge comment (WF-05 §8)."""

    def __init__(self, storage: Storage, forge: ForgePort) -> None:
        self._storage = storage
        self._forge = forge

    def investigate(self, fingerprint: Fingerprint) -> RcaResult:
        result = classify(fingerprint)
        if fingerprint.ticket_url is not None:
            comment = (
                f"**RCA** — class `{result.classification}`"
                f" · confidence {result.confidence:.2f}\n\n"
                f"{result.summary}\n\n"
                "Respond `@wakey fix` to request a suggested patch."
            )
            self._forge.add_comment(
                TicketRef(
                    issue_id=fingerprint.ticket_issue_id or fingerprint.fp_hash,
                    url=fingerprint.ticket_url,
                ),
                comment,
            )
        self._storage.record_audit(
            AuditEvent(
                actor="agent",
                action="rca.classify",
                subject=f"fp:{fingerprint.fp_hash}",
                details={"class": result.classification, "confidence": str(result.confidence)},
            )
        )
        return result
