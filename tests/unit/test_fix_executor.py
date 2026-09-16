# SPDX-License-Identifier: Apache-2.0
"""FixExecutor tests (WF-06): refusals, reservation, restore-on-failure."""

from __future__ import annotations

import json
from pathlib import Path

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

    def prepare(self, repo):
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
