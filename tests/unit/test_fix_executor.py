# SPDX-License-Identifier: Apache-2.0
"""FixExecutor tests (WF-06): refusals, reservation, restore-on-failure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import wakey.agents.fix_executor as fe_mod
import wakey.agents.workspace as ws_mod
from wakey.agents.fix import FixResult
from wakey.agents.fix_executor import FixExecutor
from wakey.agents.models import ModelReply
from wakey.agents.workspace import Workspace
from wakey.core.config import Autonomy, ServiceYaml
from wakey.core.models import Fingerprint, Service, Severity, TraceFrame, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge

REPO = "acme/fixture"


class ScriptedModel:
    """Valid JSON contract output: a failing repro, then an empty patch."""

    def complete(self, system: str, prompt: str) -> ModelReply:
        payload = json.dumps(
            {
                "repro_path": "tests/test_repro.py",
                "repro_content": "def test_repro():\n    assert False\n",
                "patches": {},
            }
        )
        return ModelReply(text=f"```json\n{payload}\n```", data={})


class PassingVerifierModel:
    def complete(self, system: str, prompt: str) -> ModelReply:
        return ModelReply(
            text='{"verdict":"pass","reason":"the minimal diff follows the RCA"}',
            data={"input_tokens": "20", "output_tokens": "8"},
        )


class BlockingVerifierModel:
    def complete(self, system: str, prompt: str) -> ModelReply:
        return ModelReply(
            text='{"verdict":"block","reason":"change is not justified by the RCA"}',
            data={},
        )


def code_fix_fingerprint() -> Fingerprint:
    return Fingerprint(
        fp_hash="e2efix0000000001",
        service="fixture",
        environment="prod",
        template="TypeError: boom",
        severity=Severity.ERROR,
        occurrences=5,
        state=WorkState.AWAITING_HUMAN,
        frames=(TraceFrame(path="app.py", line=1, function="run"),),
    )


def seed(tmp_path, repo: str = REPO):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    storage.save_fingerprint(code_fix_fingerprint())
    storage.save_service(Service(name="fixture", repo=repo, ingest_key_hash="0123456789abcdef"))
    return storage, forge


def fix_config(autonomy: Autonomy = Autonomy.FIX, test_command: str = "true") -> ServiceYaml:
    # floor lowered: the heuristic tier rates TypeError at 0.60 and these
    # tests exercise the *later* gates, not the confidence check
    return ServiceYaml(autonomy=autonomy, test_command=test_command, confidence_floor=0.5)


def test_rca_class_refusal_names_the_class(tmp_path):
    storage, forge = seed(tmp_path)
    fp = code_fix_fingerprint().model_copy(update={"template": "boom", "frames": ()})
    storage.save_fingerprint(fp)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    outcome = executor.execute(fp, fix_config(Autonomy.FIX))
    assert outcome.stage == "refused"
    assert "not code-fix" in outcome.message
    storage.close()


def test_confidence_refusal_names_the_floor(tmp_path):
    storage, forge = seed(tmp_path)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    outcome = executor.execute(code_fix_fingerprint(), ServiceYaml(autonomy=Autonomy.FIX))
    assert outcome.stage == "refused"
    assert "confidence" in outcome.message
    storage.close()


def test_refusal_outside_allowlist(tmp_path):
    storage, forge = seed(tmp_path)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=("other/repo",),
        work_root=tmp_path / "work",
    )
    outcome = executor.execute(code_fix_fingerprint(), fix_config())
    assert outcome.stage == "refused"
    assert "allowlist" in outcome.message
    storage.close()


def test_refusal_without_test_command(tmp_path):
    storage, forge = seed(tmp_path)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    config = ServiceYaml(autonomy=Autonomy.FIX, confidence_floor=0.5)  # no test_command
    outcome = executor.execute(code_fix_fingerprint(), config)
    assert outcome.stage == "refused"
    assert "test_command" in outcome.message
    storage.close()


def test_without_tests_requires_higher_confidence(tmp_path):
    storage, forge = seed(tmp_path)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    config = ServiceYaml(
        autonomy=Autonomy.FIX,
        confidence_floor=0.55,
        allow_without_tests=True,
    )
    outcome = executor.execute(code_fix_fingerprint(), config)
    assert outcome.stage == "refused"
    assert "floor 0.65" in outcome.message
    storage.close()


def test_refusal_without_model(tmp_path):
    storage, forge = seed(tmp_path)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=None,
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    outcome = executor.execute(code_fix_fingerprint(), fix_config())
    assert outcome.stage == "refused"
    assert "model" in outcome.message
    storage.close()


class FakeRuntime:
    def __init__(self, *a, **k):
        pass

    @staticmethod
    def cleanup(path):
        pass

    def prepare(self, repo):
        return Workspace(path=PREPARED_DIR_HOLDER["dir"], default_branch="main")


PREPARED_DIR_HOLDER = {"dir": Path("/tmp/nonexistent-prepared")}


def test_failed_fix_restores_open_state(tmp_path, monkeypatch):
    """The planted repro always fails and the model patches nothing: the loop
    must end 'failed' and the fingerprint must not stay stuck in `fixing`."""
    storage, forge = seed(tmp_path)
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    (workspace / "app.py").write_text("value = 1\n")
    (workspace / "test_planted.py").write_text("def test_planted():\n    assert False\n")
    PREPARED_DIR_HOLDER["dir"] = workspace

    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        runner=lambda *_: fe_mod.TestOutcome(True, "synthetic green baseline"),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    monkeypatch.setattr(ws_mod, "WorkspaceRuntime", FakeRuntime)
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", FakeRuntime)
    outcome = executor.execute(code_fix_fingerprint(), fix_config())

    assert outcome.stage == "failed"
    stored = storage.get_fingerprint("e2efix0000000001")
    assert stored is not None and stored.state is WorkState.OPEN, (
        "a failed fix must not leave the fingerprint stuck in fixing"
    )
    storage.close()


def test_executor_error_path_restores_open(tmp_path, monkeypatch):
    """A crash mid-execution reports an error and restores the state."""
    storage, forge = seed(tmp_path)

    class ExplodingRuntime:
        def __init__(self, *a, **k):
            pass

        @staticmethod
        def cleanup(path):
            pass

        def prepare(self, repo):
            raise RuntimeError("network down")

    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    monkeypatch.setattr(ws_mod, "WorkspaceRuntime", ExplodingRuntime)
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", ExplodingRuntime)
    outcome = executor.execute(code_fix_fingerprint(), fix_config())
    assert outcome.stage == "error"
    stored = storage.get_fingerprint("e2efix0000000001")
    assert stored is not None and stored.state is WorkState.OPEN
    storage.close()


class GreenRuntime:
    def __init__(self, *a, **k):
        pass

    @staticmethod
    def cleanup(path):
        pass

    def prepare(self, repo, ref=None):
        return Workspace(path=PREPARED_DIR_HOLDER["dir"], default_branch="main")


class GreenFixer:
    def __init__(self, *a, **k):
        pass

    def execute(self, ws, fp, rca, cmd):
        return FixResult(
            True,
            "repro green after 1 patch attempt(s)",
            "-- diff --",
            1,
            {"billing.py": "fixed\n"},
        )


def test_delivered_path_publishes_and_audits(tmp_path, monkeypatch):
    """Green fix + delivery: outcome delivered, forge branch + audit written."""
    storage, forge = seed(tmp_path)
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    (workspace / "app.py").write_text("value = 1\n")

    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    monkeypatch.setattr(ws_mod, "WorkspaceRuntime", GreenRuntime)
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", GreenRuntime)
    monkeypatch.setattr(fe_mod, "ReproFirstFixer", GreenFixer)

    outcome = executor.execute(code_fix_fingerprint(), fix_config())
    assert outcome.stage == "delivered"
    assert forge.branches["wakey/fix-e2efix00"]["billing.py"] == "fixed\n"

    audits = [
        r
        for r in storage._conn.execute("SELECT action FROM audit").fetchall()  # noqa: SLF001
        if r["action"] == "fix.delivered"
    ]
    assert audits, "delivery must be audited"
    storage.close()


def test_verifier_blocks_before_branch_publication(tmp_path, monkeypatch):
    storage, forge = seed(tmp_path)
    fp = code_fix_fingerprint().model_copy(
        update={"ticket_issue_id": "42", "ticket_url": "https://example.test/issues/42"}
    )
    storage.save_fingerprint(fp)
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    PREPARED_DIR_HOLDER["dir"] = workspace
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", GreenRuntime)
    monkeypatch.setattr(fe_mod, "ReproFirstFixer", GreenFixer)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        verifier_model=BlockingVerifierModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )

    outcome = executor.execute(fp, fix_config())

    assert outcome.stage == "failed" and "verifier flagged" in outcome.message
    assert forge.branches == {}
    assert forge.proposals == []
    assert forge.labels[-1][1] == "wakey/verifier-blocked"
    assert "blocked publication" in forge.comments[-1][1]
    audit = storage.list_audit(subject=f"fp:{fp.fp_hash}")
    assert any(e.action == "fix.verify" and e.details["verdict"] == "block" for e in audit)
    storage.close()


def test_allow_without_tests_publishes_labeled_untested_proposal(tmp_path, monkeypatch):
    storage, forge = seed(tmp_path)
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    (workspace / "app.py").write_text("value = 1\n")
    PREPARED_DIR_HOLDER["dir"] = workspace
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", GreenRuntime)

    class UntestedPatchModel:
        def complete(self, system: str, prompt: str) -> ModelReply:
            return ModelReply(
                text='{"patches":{"app.py":"value = 2\\n"}}',
                data={},
            )

    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=UntestedPatchModel(),
        verifier_model=PassingVerifierModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    config = ServiceYaml(
        autonomy=Autonomy.FIX,
        confidence_floor=0.5,
        allow_without_tests=True,
    )

    outcome = executor.execute(code_fix_fingerprint(), config)

    assert outcome.stage == "delivered"
    assert forge.branches["wakey/fix-e2efix00"]["app.py"] == "value = 2\n"
    labels = [label for _, label in forge.labels]
    assert "wakey/fix" in labels and "wakey/untested" in labels
    assert "Verification unavailable" in forge.proposals[0]["body"]
    assert "wakey-verifier: pass" in forge.proposals[0]["body"]
    storage.close()


def test_retry_uses_feedback_and_updates_same_branch_twice(tmp_path, monkeypatch):
    storage, forge = seed(tmp_path)
    root = tmp_path / "prepared"
    root.mkdir()
    PREPARED_DIR_HOLDER["dir"] = root
    branch = "wakey/fix-e2efix00"
    fp = code_fix_fingerprint().model_copy(
        update={
            "proposal_branch": branch,
            "proposal_url": f"console://pulls/{branch}",
            "proposal_issue_id": "7",
        }
    )
    storage.save_fingerprint(fp)
    forge.branches[branch] = {"app.py": "old\n"}
    forge.proposals.append(
        {"branch": branch, "title": "existing", "body": "existing", "base": "main"}
    )
    prepared_refs: list[str | None] = []
    summaries: list[str] = []

    class RetryRuntime(GreenRuntime):
        def prepare(self, repo, ref=None):
            prepared_refs.append(ref)
            return Workspace(
                path=PREPARED_DIR_HOLDER["dir"],
                default_branch="main",
                base_sha="a" * 40,
            )

    class RetryFixer(GreenFixer):
        def execute(self, ws, fingerprint, rca, cmd):
            summaries.append(rca)
            return super().execute(ws, fingerprint, rca, cmd)

    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", RetryRuntime)
    monkeypatch.setattr(fe_mod, "ReproFirstFixer", RetryFixer)
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedModel(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    first = executor.retry(fp, fix_config(), "handle the None case")
    second = executor.retry(fp, fix_config(), "preserve the error message")
    third = executor.retry(fp, fix_config(), "one more")

    assert first.stage == second.stage == "delivered"
    assert third.stage == "refused" and "two automated retries" in third.message
    assert prepared_refs == [branch, branch]
    assert "handle the None case" in summaries[0]
    assert "preserve the error message" in summaries[1]
    assert len(forge.proposals) == 1
    storage.close()


@pytest.mark.parametrize("scope", ["service", "../escape", "missing"])
def test_service_scope_is_enforced(tmp_path, monkeypatch, scope):
    storage, forge = seed(tmp_path)
    root = tmp_path / "prepared"
    (root / "service").mkdir(parents=True)
    PREPARED_DIR_HOLDER["dir"] = root
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", FakeRuntime)
    seen = []

    class ScopedFixer(GreenFixer):
        def execute(self, ws, fp, rca, cmd):
            seen.append(ws)
            return super().execute(ws, fp, rca, cmd)

    monkeypatch.setattr(fe_mod, "ReproFirstFixer", ScopedFixer)
    executor = FixExecutor(
        storage,
        forge,
        github_token="",
        allowed_repos=(REPO,),
        model=ScriptedModel(),
        work_root=tmp_path / "work",
    )
    outcome = executor.execute(
        code_fix_fingerprint(), fix_config().model_copy(update={"path": scope})
    )
    if scope == "service":
        assert outcome.stage == "delivered"
        assert seen == [root / "service"]
        assert forge.branches["wakey/fix-e2efix00"] == {"service/billing.py": "fixed\n"}
    else:
        assert outcome.stage == "error"
        assert seen == []
        assert forge.proposals == []
    storage.close()


def test_repository_cap_counts_other_fingerprint(tmp_path):
    storage, forge = seed(tmp_path)
    other = code_fix_fingerprint().model_copy(
        update={"fp_hash": "another-fingerprint", "proposal_branch": "wakey/other"}
    )
    storage.save_fingerprint(other)
    executor = FixExecutor(
        storage,
        forge,
        github_token="",
        allowed_repos=(REPO,),
        model=ScriptedModel(),
        work_root=tmp_path / "work",
    )
    result = executor.execute(
        code_fix_fingerprint(), fix_config().model_copy(update={"max_open_prs": 1})
    )
    assert result.stage == "refused" and "cap" in result.message
    storage.close()


def test_credential_failure_releases_fix_reservation(tmp_path):
    storage, forge = seed(tmp_path)

    def unavailable():
        raise RuntimeError("synthetic token refresh unavailable")

    executor = FixExecutor(
        storage,
        forge,
        github_token="",
        allowed_repos=(REPO,),
        model=ScriptedModel(),
        work_root=tmp_path / "work",
        github_token_provider=unavailable,
    )
    assert executor.execute(code_fix_fingerprint(), fix_config()).stage == "error"
    assert storage.get_fingerprint(code_fix_fingerprint().fp_hash).state is WorkState.OPEN
    storage.close()


def test_explicit_human_fix_overrides_chronic_triage_gate(tmp_path, monkeypatch):
    storage, forge = seed(tmp_path)
    root = tmp_path / "prepared"
    root.mkdir()
    PREPARED_DIR_HOLDER["dir"] = root
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", FakeRuntime)
    monkeypatch.setattr(fe_mod, "ReproFirstFixer", GreenFixer)
    fp = code_fix_fingerprint().model_copy(
        update={"state": WorkState.REOPENED, "chronic": True, "proposal_branch": "wakey/old"}
    )
    storage.save_fingerprint(fp)
    executor = FixExecutor(
        storage,
        forge,
        github_token="",
        allowed_repos=(REPO,),
        model=ScriptedModel(),
        work_root=tmp_path / "work",
    )
    assert executor.execute(fp, fix_config(Autonomy.TRIAGE)).stage == "refused"
    assert (
        executor.execute(fp, fix_config(Autonomy.TRIAGE), human_requested=True).stage == "delivered"
    )
    assert not storage.get_fingerprint(fp.fp_hash).chronic
    assert executor.execute(fp, fix_config(), human_requested=True).stage == "refused"
    storage.close()


def test_concurrent_fix_refused(tmp_path):
    storage, forge = seed(tmp_path)
    executor = FixExecutor(
        storage,
        forge,
        github_token="",
        allowed_repos=(REPO,),
        model=ScriptedModel(),
        work_root=tmp_path / "work",
    )
    with executor._execution_lock:
        result = executor.execute(code_fix_fingerprint(), fix_config())
    assert result.stage == "refused" and "already running" in result.message
    storage.close()
