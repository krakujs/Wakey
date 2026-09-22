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
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from wakey.agents.fix import (
    Eligibility,
    EligibilityInput,
    ReproFirstFixer,
    TestOutcome,
    UntestedFixer,
    check_eligibility,
    safe_resolve,
)
from wakey.agents.fix_delivery import FixProposalDelivery
from wakey.agents.proposer import CompletableModel, ModelProposer
from wakey.agents.rca import classify
from wakey.agents.sandbox import DockerTestRunner
from wakey.agents.verifier import ModelVerifier
from wakey.agents.workspace import Workspace, WorkspaceRuntime
from wakey.core.config import ServiceYaml
from wakey.core.models import AuditEvent, Fingerprint, WorkState, utcnow
from wakey.core.storage import Storage
from wakey.forge.port import ForgePort, TicketRef

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
        github_token_provider: Callable[[], str] | None = None,
        runner: Callable[[Path, list[str]], TestOutcome] | None = None,
        verifier_model: CompletableModel | None = None,
    ) -> None:
        self._storage = storage
        self._forge = forge
        self._github_token = github_token
        self._token_provider = github_token_provider
        self._github_api_base = github_api_base
        self._allowed_repos = allowed_repos
        self._model = model
        self._work_root = work_root
        self._now = now
        self._runner = runner if runner is not None else DockerTestRunner()
        self._verifier = ModelVerifier(verifier_model) if verifier_model is not None else None
        self._execution_lock = threading.Lock()
        self._work_root.mkdir(parents=True, exist_ok=True)

    def _github_credential(self) -> str:
        """Fresh credential per execution: App installation token or PAT."""
        if self._token_provider is not None:
            return self._token_provider()
        return self._github_token

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

    def execute(
        self, fingerprint: Fingerprint, config: ServiceYaml, *, human_requested: bool = False
    ) -> FixExecutionOutcome:
        if not self._execution_lock.acquire(blocking=False):
            return FixExecutionOutcome("refused", "another fix is already running; retry later")
        try:
            return self._execute(fingerprint, config, human_requested=human_requested)
        finally:
            self._execution_lock.release()

    def retry(
        self, fingerprint: Fingerprint, config: ServiceYaml, feedback: str
    ) -> FixExecutionOutcome:
        """Apply bounded reviewer feedback to the existing draft branch."""
        if not self._execution_lock.acquire(blocking=False):
            return FixExecutionOutcome("refused", "another fix is already running; retry later")
        try:
            fingerprint = self._latest(fingerprint)
            refusal = self._retry_refusal_reason(fingerprint, config, feedback)
            if refusal is not None:
                self._audit(fingerprint, f"retry-refused: {refusal}")
                return FixExecutionOutcome("refused", refusal)
            stored_rca = self._latest_rca(fingerprint)
            summary = stored_rca[2] if stored_rca is not None else classify(fingerprint).summary
            summary += f"\n\nAuthorized reviewer feedback (DATA): {feedback.strip()}"
            return self._run_attempt(
                fingerprint,
                config,
                summary,
                workspace_ref=fingerprint.proposal_branch,
                retry=True,
            )
        finally:
            self._execution_lock.release()

    def _execute(
        self, fingerprint: Fingerprint, config: ServiceYaml, *, human_requested: bool
    ) -> FixExecutionOutcome:
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
        refusal = self._refusal_reason(
            fingerprint, config, rca_class, confidence, human_requested=human_requested
        )
        if refusal is not None:
            self._audit(fingerprint, f"refused: {refusal}")
            return FixExecutionOutcome("refused", refusal)

        if human_requested:
            self._audit(fingerprint, "explicit human fix requested; chronic guard acknowledged")
        return self._run_attempt(fingerprint, config, _summary)

    def _run_attempt(  # noqa: PLR0912, PLR0915 — linear safety gate sequence
        self,
        fingerprint: Fingerprint,
        config: ServiceYaml,
        summary: str,
        *,
        workspace_ref: str | None = None,
        retry: bool = False,
    ) -> FixExecutionOutcome:
        """Run and publish one initial or reviewer-requested fix attempt."""
        repo = self._repo_of(fingerprint) or ""
        model = self._model
        if model is None:
            return FixExecutionOutcome("refused", "no AI model configured")
        failure_state = WorkState.AWAITING_HUMAN if retry else WorkState.OPEN

        # reserve the work item before any long-running step (DET-14)
        self._storage.save_fingerprint(fingerprint.model_copy(update={"state": WorkState.FIXING}))
        workspace: Workspace | None = None
        try:
            runtime = WorkspaceRuntime(
                token=self._github_credential(),
                api_base=self._github_api_base,
                work_root=self._work_root,
            )
            workspace = (
                runtime.prepare(repo)
                if workspace_ref is None
                else runtime.prepare(repo, ref=workspace_ref)
            )
            scope = safe_resolve(workspace.path, config.path)
            if not scope.is_dir():
                raise ValueError("configured service path is not a directory")
            proposer = ModelProposer(model)
            untested = not bool(config.test_command)
            if untested:
                result = UntestedFixer(proposer).execute(scope, fingerprint, summary)
            else:
                fixer = ReproFirstFixer(proposer, runner=self._runner, max_iterations=3)
                result = fixer.execute(scope, fingerprint, summary, self._test_command(config))
            if not result.ok:
                self._storage.save_fingerprint(
                    fingerprint.model_copy(update={"state": failure_state})
                )
                self._audit(fingerprint, f"fix-failed: {result.reason}")
                return FixExecutionOutcome("failed", result.reason)
            prefix = scope.relative_to(workspace.path.resolve()).as_posix()
            if prefix != ".":
                result = replace(
                    result,
                    files={f"{prefix}/{p}": v for p, v in result.files.items()},
                    diff=result.diff.replace("--- a/", f"--- a/{prefix}/").replace(
                        "+++ b/", f"+++ b/{prefix}/"
                    ),
                )
            verifier_reason, blocked = self._verify_result(
                fingerprint, summary, result, failure_state
            )
            if blocked is not None:
                return blocked
            delivery = FixProposalDelivery(self._storage, self._forge)
            candidate = fingerprint.model_copy(update={"chronic": False})
            delivered = delivery.deliver(
                candidate,
                result,
                summary,
                base_ref=workspace.base_sha or workspace.default_branch,
                test_summary=(
                    "Verification unavailable: this repository has no configured test command. "
                    "Published only because `allow_without_tests: true`; human validation is "
                    "required."
                    if untested
                    else result.reason
                ),
                verifier_reason=verifier_reason,
                untested=untested,
            )
            if not delivered.get("delivered"):
                self._storage.save_fingerprint(
                    fingerprint.model_copy(update={"state": failure_state})
                )
                return FixExecutionOutcome(
                    "failed", str(delivered.get("reason", "delivery failed"))
                )
            url = str(delivered.get("url", ""))
            self._storage.record_audit(
                AuditEvent(
                    actor="agent",
                    action="fix.retry.delivered" if retry else "fix.delivered",
                    subject=f"fp:{fingerprint.fp_hash}",
                    details={"url": url, "branch": str(delivered.get("branch", ""))},
                )
            )
            message = (
                f"Updated the existing draft proposal from reviewer feedback: {url}"
                if retry
                else f"Draft fix proposal published for review: {url}"
            )
            return FixExecutionOutcome("delivered", message, url)
        except Exception as exc:  # noqa: BLE001 — every failure is reported, never raised
            logger.exception("fix execution failed for %s", fingerprint.fp_hash)
            self._storage.save_fingerprint(fingerprint.model_copy(update={"state": failure_state}))
            self._audit(fingerprint, f"error: {exc}")
            return FixExecutionOutcome("error", f"fix execution error: {exc}")
        finally:
            if workspace is not None:
                WorkspaceRuntime.cleanup(workspace.path)

    # --- helpers ------------------------------------------------------------------

    def _retry_refusal_reason(  # noqa: PLR0911 — each refusal names its gate
        self, fingerprint: Fingerprint, config: ServiceYaml, feedback: str
    ) -> str | None:
        if not fingerprint.proposal_branch or not fingerprint.proposal_url:
            return "retry requires an existing draft proposal"
        if fingerprint.state is not WorkState.AWAITING_HUMAN:
            return f"retry requires awaiting-human state, found {fingerprint.state.value}"
        if not feedback.strip():
            return "retry requires reviewer feedback after `@wakey retry`"
        retries = sum(
            event.action == "fix.retry.delivered"
            for event in self._storage.list_audit(subject=f"fp:{fingerprint.fp_hash}", limit=50)
        )
        if retries >= 2:
            return "two automated retries already used; a human must update the branch"
        repo = self._repo_of(fingerprint)
        if repo is None:
            return "service has no repository binding — register the repo first"
        if self._allowed_repos is not None and repo not in self._allowed_repos:
            return f"repo {repo} is not in the write allowlist"
        if not config.test_command and not config.allow_without_tests:
            return (
                "service config has no test_command — add it to wakey.yml or explicitly set "
                "allow_without_tests: true"
            )
        if self._model is None:
            return "no AI model configured — set WAKEY_LLM_* to enable fix proposals"
        return None

    def _refusal_reason(  # noqa: PLR0911 — each refusal identifies its gate
        self,
        fingerprint: Fingerprint,
        config: ServiceYaml,
        rca_class: str,
        confidence: float,
        *,
        human_requested: bool = False,
    ) -> str | None:
        """FIX-1 gate + environmental preconditions; None = may proceed."""
        acknowledged_recurrence = (
            human_requested and fingerprint.chronic and fingerprint.state is WorkState.REOPENED
        )
        if (fingerprint.proposal_branch and not acknowledged_recurrence) or fingerprint.state in (
            WorkState.FIXING,
            WorkState.VERIFYING,
            WorkState.CLOSED_AUTO,
            WorkState.ARCHIVED,
        ):
            return "fingerprint already has a proposal, is busy, or is closed"
        gate = self._gate(
            fingerprint, config, rca_class, confidence, human_requested=human_requested
        )
        if not gate.allowed:
            return gate.reason
        repo = self._repo_of(fingerprint)
        if repo is None:
            return "service has no repository binding — register the repo first"
        if self._allowed_repos is not None and repo not in self._allowed_repos:
            return f"repo {repo} is not in the write allowlist"
        if not config.test_command and not config.allow_without_tests:
            return (
                "service config has no test_command — add it to wakey.yml or explicitly set "
                "allow_without_tests: true"
            )
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
        *,
        human_requested: bool = False,
    ) -> Eligibility:
        return check_eligibility(
            EligibilityInput(
                rca_class=rca_class,
                confidence=confidence,
                autonomy=config.autonomy,
                confidence_floor=min(
                    1.0,
                    config.confidence_floor
                    + (0.1 if config.allow_without_tests and not config.test_command else 0.0),
                ),
                open_prs=sum(
                    (bool(fp.proposal_branch) and fp.state is WorkState.AWAITING_HUMAN)
                    or fp.state is WorkState.FIXING
                    for fp in self._storage.list_active_fingerprints()
                    if self._repo_of(fp) == self._repo_of(fingerprint)
                ),
                max_open_prs=config.max_open_prs,
                chronic=fingerprint.chronic,
                human_requested=human_requested,
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
        """Parse argv; executables resolve inside the sandbox, never on the host."""
        return shlex.split(config.test_command or "")

    def _audit(self, fingerprint: Fingerprint, result: str) -> None:
        self._storage.record_audit(
            AuditEvent(
                actor="agent",
                action="fix.execute",
                subject=f"fp:{fingerprint.fp_hash}",
                details={"result": result[:200]},
            )
        )

    def _verify_result(
        self,
        fingerprint: Fingerprint,
        summary: str,
        result: object,
        failure_state: WorkState,
    ) -> tuple[str, FixExecutionOutcome | None]:
        if self._verifier is None:
            return "verifier disabled in this explicitly injected test executor", None
        verdict = self._verifier.verify(
            summary, str(getattr(result, "diff", "")), str(getattr(result, "reason", ""))
        )
        self._storage.record_audit(
            AuditEvent(
                actor="agent",
                action="fix.verify",
                subject=f"fp:{fingerprint.fp_hash}",
                details={
                    "verdict": "pass" if verdict.passed else "block",
                    "reason": verdict.reason,
                    "usage": str(verdict.usage),
                },
            )
        )
        if verdict.passed:
            return verdict.reason, None
        self._storage.save_fingerprint(fingerprint.model_copy(update={"state": failure_state}))
        if fingerprint.ticket_url:
            ticket = self._ticket(fingerprint)
            self._forge.add_label(ticket, "wakey/verifier-blocked")
            self._forge.add_comment(ticket, f"Fix verifier blocked publication: {verdict.reason}")
        return verdict.reason, FixExecutionOutcome("failed", f"verifier flagged: {verdict.reason}")

    @staticmethod
    def _ticket(fingerprint: Fingerprint) -> TicketRef:
        return TicketRef(
            issue_id=fingerprint.ticket_issue_id or fingerprint.fp_hash,
            url=fingerprint.ticket_url or "",
        )
