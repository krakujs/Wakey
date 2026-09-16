# SPDX-License-Identifier: Apache-2.0
"""Fix execution runtime (WF-06, E7-T1/E8-T5): eligibility in, draft PR out.

The single entry point behind ``@wakey fix`` and the dashboard's fix
trigger. Sequencing: FIX-1 eligibility gate (autonomy dial, chronic
guard, PR cap) → per-repo GitHub adapter (allowlist enforced) →
workspace fetch → repro-first fixer with the live model proposer →
branch + draft PR publication → forge comment with the review link.

Every outcome — refusal, failure, or delivery — is audited and, when a
ticket exists, posted back to it. Live fixing only runs for services
whose *stored config* sets ``autonomy: fix`` and a ``test_command``; the
per-repo write allowlist is enforced on every adapter this module builds.
"""

from __future__ import annotations

import logging
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from wakey.agents.fix import (
    Eligibility,
    EligibilityInput,
    ReproFirstFixer,
    check_eligibility,
)
from wakey.agents.fix_delivery import FixProposalDelivery
from wakey.agents.proposer import CompletableModel, ModelProposer
from wakey.agents.rca import classify
from wakey.agents.workspace import Workspace, WorkspaceRuntime
from wakey.core.config import ServiceYaml
from wakey.core.models import AuditEvent, Fingerprint, WorkState, utcnow
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixExecutionOutcome:
    """What happened, surfaced to the command bus / dashboard / audit."""

    stage: str  # delivered | refused | failed | error
    message: str
    url: str = ""


class FixExecutor:
    """Runs one human-requested fix attempt for one fingerprint."""

    def __init__(
        self,
        storage: Storage,
        forge: ForgePort,
        *,
        github_token: str,
        github_api_base: str = "https://api.github.com",
        allowed_repos: tuple[str, ...] | None,
        model: CompletableModel | None = None,
        work_root: Path,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._forge = forge
        self._github_token = github_token
        self._github_api_base = github_api_base
        self._allowed_repos = allowed_repos
        self._model = model
        self._work_root = work_root
        self._now = now
        self._work_root.mkdir(parents=True, exist_ok=True)

    def _latest_rca(self, fingerprint: Fingerprint) -> tuple[str, float, str] | None:
        """Newest stored RCA verdict (model-refined when available)."""
        for event in self._storage.list_audit(subject=f"fp:{fingerprint.fp_hash}", limit=50):
            if event.action != "rca.classify":
                continue
            details = event.details
            try:
                confidence = float(str(details.get("confidence", "0")))
            except ValueError:
                confidence = 0.0
            rca_class = str(details.get("class", "needs-human"))
            summary = str(details.get("summary", ""))
            return (rca_class, confidence, summary)
        return None

    def execute(self, fingerprint: Fingerprint, config: ServiceYaml) -> FixExecutionOutcome:
        fingerprint = self._latest(fingerprint)
        stored_rca = self._latest_rca(fingerprint)
        if stored_rca is not None:
            rca_class, confidence, _summary = stored_rca
        else:
            heuristic = classify(fingerprint)
            rca_class, confidence, _summary = (
                heuristic.classification,
                heuristic.confidence,
                heuristic.summary,
            )
        refusal = self._refusal_reason(fingerprint, config, rca_class, confidence)
        if refusal is not None:
            self._audit(fingerprint, f"refused: {refusal}")
            return FixExecutionOutcome("refused", refusal)

        repo = self._repo_of(fingerprint) or ""
        model = self._model
        if model is None:  # guaranteed absent by _refusal_reason, re-checked for mypy
            return FixExecutionOutcome("refused", "no AI model configured")

        # reserve the work item before any long-running step (DET-14)
        self._storage.save_fingerprint(fingerprint.model_copy(update={"state": WorkState.FIXING}))
        runtime = WorkspaceRuntime(
            token=self._github_token,
            api_base=self._github_api_base,
            work_root=self._work_root,
        )
        workspace: Workspace | None = None
        try:
            workspace = runtime.prepare(repo)
            fixer = ReproFirstFixer(ModelProposer(model), max_iterations=3)
            test_command = self._test_command(config)
            result = fixer.execute(workspace.path, fingerprint, "", test_command)
            if not result.ok:
                self._storage.save_fingerprint(
                    fingerprint.model_copy(update={"state": WorkState.OPEN})
                )
                self._audit(fingerprint, f"fix-failed: {result.reason}")
                return FixExecutionOutcome("failed", result.reason)
            delivery = FixProposalDelivery(self._storage, self._forge)
            delivered = delivery.deliver(fingerprint, result, "Model-generated fix (live run)")
            if not delivered.get("delivered"):
                return FixExecutionOutcome(
                    "failed", str(delivered.get("reason", "delivery failed"))
                )
            url = str(delivered.get("url", ""))
            self._storage.record_audit(
                AuditEvent(
                    actor="agent",
                    action="fix.delivered",
                    subject=f"fp:{fingerprint.fp_hash}",
                    details={"url": url, "branch": str(delivered.get("branch", ""))},
                )
            )
            return FixExecutionOutcome(
                "delivered", f"Draft fix proposal published for review: {url}", url
            )
        except Exception as exc:  # noqa: BLE001 — every failure is reported, never raised
            logger.exception("fix execution failed for %s", fingerprint.fp_hash)
            self._storage.save_fingerprint(fingerprint.model_copy(update={"state": WorkState.OPEN}))
            self._audit(fingerprint, f"error: {exc}")
            return FixExecutionOutcome("error", f"fix execution error: {exc}")
        finally:
            if workspace is not None:
                WorkspaceRuntime.cleanup(workspace.path)

    # --- helpers ------------------------------------------------------------------

    def _refusal_reason(
        self,
        fingerprint: Fingerprint,
        config: ServiceYaml,
        rca_class: str,
        confidence: float,
    ) -> str | None:
        """FIX-1 gate + environmental preconditions; None = may proceed."""
        gate = self._gate(fingerprint, config, rca_class, confidence)
        if not gate.allowed:
            return gate.reason
        repo = self._repo_of(fingerprint)
        if repo is None:
            return "service has no repository binding — register the repo first"
        if self._allowed_repos is not None and repo not in self._allowed_repos:
            return f"repo {repo} is not in the write allowlist"
        if not config.test_command:
            return "service config has no test_command — add it to wakey.yml"
        if self._model is None:
            return "no AI model configured — set WAKEY_LLM_* to enable fix proposals"
        return None

    def _latest(self, fingerprint: Fingerprint) -> Fingerprint:
        stored = self._storage.get_fingerprint(fingerprint.fp_hash)
        return stored or fingerprint

    def _gate(
        self,
        fingerprint: Fingerprint,
        config: ServiceYaml,
        rca_class: str,
        confidence: float,
    ) -> Eligibility:
        return check_eligibility(
            EligibilityInput(
                rca_class=rca_class,
                confidence=confidence,
                autonomy=config.autonomy,
                confidence_floor=config.confidence_floor,
                open_prs=1 if fingerprint.proposal_branch else 0,
                max_open_prs=config.max_open_prs,
                chronic=fingerprint.chronic,
            )
        )

    def _repo_of(self, fingerprint: Fingerprint) -> str | None:
        service = self._storage.get_service(fingerprint.service)
        return service.repo if service else None

    def _resolve_repo(self, fingerprint: Fingerprint, config: ServiceYaml) -> tuple[str, str]:
        """(repo, failure) — allowlist-checked repository binding."""
        repo = self._repo_of(fingerprint)
        if repo is None:
            return "", "service has no repository binding"
        if self._allowed_repos is not None and repo not in self._allowed_repos:
            return "", f"repo {repo} is not in the write allowlist"
        return repo, ""

    @staticmethod
    def _test_command(config: ServiceYaml) -> list[str]:
        """Parse the test command, pinning bare `python` to our interpreter.

        The fixer runs tests with this runtime's Python (which carries the
        service's dependencies after workspace bootstrap), so a bare
        `python`/`python3` in the configured command must resolve here.
        """
        tokens = shlex.split(config.test_command or "")
        if tokens and tokens[0] in ("python", "python3"):
            tokens[0] = sys.executable
        return tokens

    def _audit(self, fingerprint: Fingerprint, result: str) -> None:
        self._storage.record_audit(
            AuditEvent(
                actor="agent",
                action="fix.execute",
                subject=f"fp:{fingerprint.fp_hash}",
                details={"result": result[:200]},
            )
        )
