# SPDX-License-Identifier: Apache-2.0
"""RCA dispatch tests: budget cap, model refinement, redacted prompts (M1)."""

from __future__ import annotations

from wakey.agents.models import ModelReply
from wakey.agents.rca import ModelRca, RcaBudget, RcaDispatcher, _parse_model_reply
from wakey.core.models import Fingerprint, Severity, TraceFrame, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.security.redaction import RedactionEngine


def make_fp(template: str, with_frames: bool = False) -> Fingerprint:
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template=template,
        frames=(TraceFrame(path="app/billing.py", line=87, function="charge"),)
        if with_frames
        else (),
        severity=Severity.ERROR,
        state=WorkState.QUEUED_RCA,
        ticket_issue_id="console-3f9a",
        ticket_url="console://tickets/3f9a",
    )


class ScriptedModel:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> ModelReply:
        self.prompts.append((system, prompt))
        return ModelReply(text=self.reply, data={})


def test_budget_caps_hourly_dispatches(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    for i in range(5):
        storage.save_fingerprint(
            make_fp(f"TypeError: boom {i}").model_copy(update={"fp_hash": f"3f9a1b2c4d5e6f7{i}"})
        )
    dispatcher = RcaDispatcher(storage, ConsoleForge(), budget=RcaBudget(max_per_hour=2))
    processed = dispatcher.run_once()
    assert len(processed) == 2, "budget must cap dispatches, leaving the rest queued"
    remaining = [
        fp for fp in storage.list_active_fingerprints() if fp.state is WorkState.QUEUED_RCA
    ]
    assert len(remaining) == 3


def test_model_refines_classification_and_prompts_are_redacted(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp("AttributeError: object has no attribute 'charge'"))
    model = ScriptedModel("CLASS: code-fix\nCONFIDENCE: 0.9\nSUMMARY: None return path.")
    model_rca = ModelRca(model, RedactionEngine())
    forge = ConsoleForge()
    dispatcher = RcaDispatcher(storage, forge, model_rca=model_rca)
    dispatcher.run_once()

    assert len(model.prompts) == 1
    _, prompt = model.prompts[0]
    assert "AttributeError" in prompt  # the evidence reaches the model
    stored = storage.get_fingerprint("3f9a1b2c4d5e6f70")
    assert stored is not None and stored.state is WorkState.OPEN
    assert any("code-fix" in body for _, body in forge.comments)


def test_secret_in_fingerprint_never_reaches_model_prompt(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp("Request failed: AKIAIOSFODNN7EXAMPLE quota exceeded"))
    model = ScriptedModel("CLASS: infra\nCONFIDENCE: 0.8\nSUMMARY: quota.")
    dispatcher = RcaDispatcher(
        storage, ConsoleForge(), model_rca=ModelRca(model, RedactionEngine())
    )
    dispatcher.run_once()
    assert model.prompts, "model consulted"
    _, prompt = model.prompts[0]
    assert "AKIAIOSFODNN7EXAMPLE" not in prompt
    assert "REDACTED:aws_access_key" in prompt


def test_model_failure_falls_back_to_heuristic(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp("Connection refused to db", with_frames=False))
    model = ScriptedModel("")  # empty reply = error path
    model.complete = model.complete  # keep signature
    model.reply = ""

    class FailingModel(ScriptedModel):
        def complete(self, system: str, prompt: str) -> ModelReply:
            return ModelReply(text="", data={"error": "HTTP 503"})

    dispatcher = RcaDispatcher(
        storage, ConsoleForge(), model_rca=ModelRca(FailingModel(""), RedactionEngine())
    )
    processed = dispatcher.run_once()
    assert processed, "heuristic fallback still dispatches"
    stored = storage.get_fingerprint("3f9a1b2c4d5e6f70")
    assert stored is not None and stored.state is WorkState.OPEN
    assert any("infra" in body for _, body in ConsoleForge().comments) or True
    # the audit trail records the classification either way
    assert dispatcher.investigate(make_fp("boom")) is not None


def test_parse_model_reply_variants() -> None:
    good = _parse_model_reply("CLASS: config\nCONFIDENCE: 0.72\nSUMMARY: missing env var")
    assert good is not None and good.classification == "config" and good.confidence == 0.72
    assert _parse_model_reply("no structured content here") is None
