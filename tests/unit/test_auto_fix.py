# SPDX-License-Identifier: Apache-2.0
"""Unattended fix dispatch (WF-06 §2): autonomy=fix runs RCA → fix → draft PR
with no human trigger; every lower dial stays read-only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

import wakey.agents.fix_executor as fe_mod
import wakey.agents.workspace as ws_mod
from wakey.agents.fix import FixResult
from wakey.agents.fix_executor import FixExecutor
from wakey.agents.models import ModelReply
from wakey.agents.rca import ModelRca, RcaDispatcher
from wakey.agents.workspace import Workspace
from wakey.core.composition import build_fix_trigger
from wakey.core.config import Autonomy, ServiceYaml
from wakey.core.models import Fingerprint, Service, Severity, TraceFrame, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.security.redaction import RedactionEngine

REPO = "acme/fixture"


class ScriptedRca:
    def complete(self, system: str, prompt: str) -> ModelReply:
        return ModelReply(
            text="CLASS: code-fix\nCONFIDENCE: 0.9\nSUMMARY: null return path.",
            data={},
        )


class ScriptedFixer:
    def complete(self, system: str, prompt: str) -> ModelReply:
        payload = json.dumps(
            {
                "repro_path": "tests/test_repro.py",
                "repro_content": "def test_repro():\n    assert False\n",
                "patches": {},
            }
        )
        return ModelReply(text=f"```json\n{payload}\n```", data={})


class GreenRuntime:
    def __init__(self, *a, **k):
        pass

    @staticmethod
    def cleanup(path):
        pass

    def prepare(self, repo):
        return Workspace(path=PREPARED["dir"], default_branch="main")


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


PREPARED: dict[str, Path] = {"dir": Path("/tmp/nonexistent")}


def make_fp(chronic: bool = False) -> Fingerprint:
    return Fingerprint(
        fp_hash="autofix0000000001",
        service="fixture",
        environment="prod",
        template="TypeError: boom",
        severity=Severity.ERROR,
        occurrences=4,
        state=WorkState.QUEUED_RCA,
        chronic=chronic,
        frames=(TraceFrame(path="app.py", line=1, function="run"),),
        ticket_issue_id="console-autofix",
        ticket_url="console://tickets/autofix",
    )


def seed(tmp_path: Path, autonomy: Autonomy) -> SQLiteStorage:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    config = ServiceYaml(autonomy=autonomy, test_command="true", confidence_floor=0.5)
    storage.save_service(
        Service(
            name="fixture",
            repo=REPO,
            ingest_key_hash="0123456789abcdef",
            config_json=config.model_dump_json(),
        )
    )
    storage.save_fingerprint(make_fp())
    return storage


def build(storage: SQLiteStorage, tmp_path: Path) -> tuple[RcaDispatcher, ConsoleForge]:
    """The production wiring: dispatcher + executor share a forge and the
    composition-level trigger (not a test-local mirror)."""
    forge = ConsoleForge()
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedFixer(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    dispatcher = RcaDispatcher(
        storage,
        forge,
        model_rca=ModelRca(ScriptedRca(), RedactionEngine()),
        auto_fix=build_fix_trigger(storage, executor),
    )
    return dispatcher, forge


@pytest.fixture()
def green(monkeypatch, tmp_path):
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    (workspace / "app.py").write_text("value = 1\n")
    PREPARED["dir"] = workspace
    monkeypatch.setattr(ws_mod, "WorkspaceRuntime", GreenRuntime)
    monkeypatch.setattr(fe_mod, "WorkspaceRuntime", GreenRuntime)
    monkeypatch.setattr(fe_mod, "ReproFirstFixer", GreenFixer)


def audit_actions(storage: SQLiteStorage) -> set[str]:
    rows = storage._conn.execute("SELECT action FROM audit").fetchall()  # noqa: SLF001
    return {cast("str", r["action"]) for r in rows}


def test_autonomy_fix_delivers_pr_with_no_human_trigger(tmp_path, green):
    """The headline path: ingest → RCA → draft PR, zero human steps."""
    storage = seed(tmp_path, Autonomy.FIX)
    dispatcher, forge = build(storage, tmp_path)
    dispatcher.run_once()

    stored = storage.get_fingerprint("autofix0000000001")
    assert stored is not None and stored.state is WorkState.AWAITING_HUMAN, (
        "delivered auto-fix parks on awaiting-human (review, never merge)"
    )
    assert forge.proposals, "draft proposal published"
    actions = audit_actions(storage)
    assert "rca.classify" in actions and "fix.delivered" in actions
    storage.close()


def test_autonomy_triage_never_attempts_a_fix(tmp_path, green):
    storage = seed(tmp_path, Autonomy.TRIAGE)
    dispatcher, forge = build(storage, tmp_path)
    dispatcher.run_once()

    stored = storage.get_fingerprint("autofix0000000001")
    assert stored is not None and stored.state is WorkState.OPEN, "RCA still completes"
    assert not forge.branches and not forge.proposals, (
        "triage dial must not create fix branches or proposals"
    )
    storage.close()


def test_chronic_fingerprint_skips_auto_fix(tmp_path, green):
    """Chronic regressions need a human; the dispatcher must not dispatch."""
    storage = seed(tmp_path, Autonomy.FIX)
    storage.save_fingerprint(make_fp(chronic=True))
    dispatcher, forge = build(storage, tmp_path)
    dispatcher.run_once()

    stored = storage.get_fingerprint("autofix0000000001")
    assert stored is not None and stored.state is WorkState.OPEN
    assert not forge.branches, "chronic regressions must not get auto-fixed"
    storage.close()


def test_low_confidence_verdict_stops_at_rca(tmp_path, green):
    """Below the service floor the executor refuses: no branch, state OPEN."""
    storage = seed(tmp_path, Autonomy.FIX)

    class ShakyRca(ScriptedRca):
        def complete(self, system: str, prompt: str) -> ModelReply:
            reply = super().complete(system, prompt)
            return ModelReply(text=reply.text.replace("0.9", "0.3"), data={})

    forge = ConsoleForge()
    executor = FixExecutor(
        storage,
        forge,
        github_token="tok",
        model=ScriptedFixer(),
        allowed_repos=(REPO,),
        work_root=tmp_path / "work",
    )
    dispatcher = RcaDispatcher(
        storage,
        forge,
        model_rca=ModelRca(ShakyRca(), RedactionEngine()),
        auto_fix=build_fix_trigger(storage, executor),
    )
    dispatcher.run_once()

    stored = storage.get_fingerprint("autofix0000000001")
    assert stored is not None and stored.state is WorkState.OPEN
    assert not forge.branches, "below-floor verdicts must not reach a workspace"
    storage.close()
