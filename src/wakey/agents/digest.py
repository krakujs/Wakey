# SPDX-License-Identifier: Apache-2.0
"""Daily, per-repository approval digest for ``autonomy: digest`` (FIX-9)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from wakey.agents.fix_executor import FixExecutionOutcome
from wakey.core.config import Autonomy, ServiceYaml
from wakey.core.models import AuditEvent, Fingerprint, WorkState, utcnow
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort


@dataclass(frozen=True)
class DigestOutcome:
    """Result of creating or finding today's repository digest."""

    created: bool
    message: str
    issue_id: str = ""
    url: str = ""


class DigestManager:
    """Collect eligible digest candidates and release them after approval."""

    def __init__(
        self,
        storage: Storage,
        forge: ForgePort,
        config_for: Callable[[str], ServiceYaml],
        execute: Callable[[Fingerprint, ServiceYaml], FixExecutionOutcome],
        *,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._forge = forge
        self._config_for = config_for
        self._execute = execute
        self._now = now

    def run_once(self, repo: str | None = None) -> list[DigestOutcome]:
        """Create at most one digest per repository and UTC day."""
        grouped: dict[str, list[Fingerprint]] = {}
        for fingerprint in self._storage.list_active_fingerprints():
            service = self._storage.get_service(fingerprint.service)
            if service is None or (repo is not None and service.repo != repo):
                continue
            if self._eligible(fingerprint, self._config_for(fingerprint.service)):
                grouped.setdefault(service.repo, []).append(fingerprint)
        return [self._ensure_digest(name, candidates) for name, candidates in grouped.items()]

    def approve(self, issue_id: str, selector: str) -> FixExecutionOutcome:
        """Release one exact candidate from a digest after command authorization."""
        event = self._digest_by_issue(issue_id)
        if event is None:
            return FixExecutionOutcome("refused", "this issue is not a Wakey approval digest")
        candidates = str(event.details.get("candidates", "")).split(",")
        matches = [item for item in candidates if item and item.startswith(selector.strip())]
        if len(matches) != 1:
            return FixExecutionOutcome(
                "refused", "approval must name one unambiguous fingerprint from this digest"
            )
        fp_hash = matches[0]
        if self._already_released(event.subject, fp_hash):
            return FixExecutionOutcome("refused", "that digest candidate was already released")
        fingerprint = self._storage.get_fingerprint(fp_hash)
        if fingerprint is None:
            return FixExecutionOutcome("refused", "digest candidate no longer exists")
        config = self._config_for(fingerprint.service)
        outcome = self._execute(fingerprint, config)
        self._storage.record_audit(
            AuditEvent(
                actor="user",
                action="digest.release",
                subject=event.subject,
                details={"fingerprint": fp_hash, "result": outcome.stage},
            )
        )
        return outcome

    def _ensure_digest(self, repo: str, candidates: list[Fingerprint]) -> DigestOutcome:
        subject = self._subject(repo)
        existing = next(
            (
                event
                for event in self._storage.list_audit(subject=subject, limit=20)
                if event.action == "digest.create"
            ),
            None,
        )
        if existing is not None:
            return DigestOutcome(
                False,
                "Today's digest already exists.",
                str(existing.details.get("issue_id", "")),
                str(existing.details.get("url", "")),
            )
        candidates.sort(key=lambda item: (-item.severity.rank, -item.occurrences, item.fp_hash))
        body = self._body(repo, candidates)
        ref = self._forge.create_ticket(
            candidates[0], f"[wakey] Fix approval digest — {self._now().date().isoformat()}", body
        )
        self._forge.add_label(ref, "wakey/digest")
        self._storage.record_audit(
            AuditEvent(
                actor="system",
                action="digest.create",
                subject=subject,
                details={
                    "issue_id": ref.issue_id,
                    "url": ref.url,
                    "candidates": ",".join(item.fp_hash for item in candidates),
                },
            )
        )
        return DigestOutcome(True, f"Digest created: {ref.url}", ref.issue_id, ref.url)

    def _eligible(self, fingerprint: Fingerprint, config: ServiceYaml) -> bool:
        if config.autonomy is not Autonomy.DIGEST:
            return False
        if fingerprint.proposal_branch or fingerprint.chronic:
            return False
        if fingerprint.state in {
            WorkState.FIXING,
            WorkState.VERIFYING,
            WorkState.CLOSED_AUTO,
            WorkState.CLOSED_HUMAN,
            WorkState.DROPPED,
            WorkState.VERIFIED_CLOSED,
            WorkState.ARCHIVED,
        }:
            return False
        rca = self._latest_rca(fingerprint)
        if rca is None or rca[0] != "code-fix":
            return False
        effective_floor = min(
            1.0,
            config.confidence_floor
            + (0.1 if config.allow_without_tests and not config.test_command else 0.0),
        )
        return rca[1] >= effective_floor and bool(config.test_command or config.allow_without_tests)

    def _latest_rca(self, fingerprint: Fingerprint) -> tuple[str, float] | None:
        for event in self._storage.list_audit(subject=f"fp:{fingerprint.fp_hash}", limit=50):
            if event.action != "rca.classify":
                continue
            try:
                return str(event.details.get("class", "")), float(
                    str(event.details.get("confidence", "0"))
                )
            except ValueError:
                return None
        return None

    def _digest_by_issue(self, issue_id: str) -> AuditEvent | None:
        return next(
            (
                event
                for event in self._storage.list_audit(limit=500)
                if event.action == "digest.create"
                and str(event.details.get("issue_id", "")) == issue_id
            ),
            None,
        )

    def _already_released(self, subject: str, fp_hash: str) -> bool:
        return any(
            event.action == "digest.release"
            and event.details.get("fingerprint") == fp_hash
            and event.details.get("result") == "delivered"
            for event in self._storage.list_audit(subject=subject, limit=100)
        )

    def _subject(self, repo: str) -> str:
        day = self._now().astimezone(UTC).date().isoformat()
        return f"digest:{repo}:{day}"

    @staticmethod
    def _body(repo: str, candidates: list[Fingerprint]) -> str:
        lines = [
            f"Daily fix candidates for `{repo}`. Human approval is required.",
            "",
        ]
        for fingerprint in candidates:
            lines.append(
                f"- `fp:{fingerprint.fp_hash}` — {fingerprint.service}, "
                f"{fingerprint.severity.value}, ×{fingerprint.occurrences}; "
                f"ticket {fingerprint.ticket_url or '(local)'}"
            )
        lines.extend(
            (
                "",
                "Approve exactly one candidate with `@wakey approve <fingerprint>`.",
                "Wakey creates a draft proposal and never merges it.",
            )
        )
        return "\n".join(lines)
