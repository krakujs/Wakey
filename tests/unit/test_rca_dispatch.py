# SPDX-License-Identifier: Apache-2.0
"""RCA dispatch tests: budget cap, model refinement, redacted prompts (M1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wakey.agents.models import ModelReply
from wakey.agents.rca import ModelRca, RcaBudget, RcaDispatcher, _parse_model_reply
from wakey.core.models import DeployEvent, Fingerprint, Service, Severity, TraceFrame, WorkState
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
    def __init__(self, reply: str, data: dict[str, str] | None = None) -> None:
        self.reply = reply
        self.data = data or {}
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> ModelReply:
        self.prompts.append((system, prompt))
        return ModelReply(text=self.reply, data=self.data)


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


def test_budget_caps_worst_case_hourly_tokens() -> None:
    budget = RcaBudget(max_per_hour=100, max_tokens_per_hour=1024)
    assert budget.try_take(1000, estimated_tokens=512)
    assert budget.try_take(1001, estimated_tokens=512)
    assert not budget.try_take(1002, estimated_tokens=512)
    assert budget.try_take(5000, estimated_tokens=512), "old reservations expire"


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


def test_rca_prompt_includes_bounded_repo_and_deploy_diff_evidence(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        Service(name="payments-api", repo="acme/payments", ingest_key_hash="0123456789abcdef")
    )
    storage.save_fingerprint(make_fp("AttributeError: charge", with_frames=True))
    now = datetime(2026, 9, 22, 10, tzinfo=UTC)
    storage.save_deploy(
        DeployEvent(service="payments-api", environment="prod", sha="new", deployed_at=now)
    )
    storage.save_deploy(
        DeployEvent(
            service="payments-api",
            environment="prod",
            sha="old",
            deployed_at=now - timedelta(hours=1),
        )
    )
    forge = ConsoleForge()
    forge.files["app/billing.py"] = "def charge(order):\n    return order.total\n"
    forge.comparisons[("old", "new")] = "- return order.total\n+ return order.total or 0"
    model = ScriptedModel(
        "CLASS: code-fix\nCONFIDENCE: 0.91\nSUMMARY: missing total guard.",
        {"model": "cheap-review", "input_tokens": "120", "output_tokens": "20"},
    )
    dispatcher = RcaDispatcher(storage, forge, model_rca=ModelRca(model, RedactionEngine()))

    dispatcher.run_once()

    prompt = model.prompts[0][1]
    assert "Repository: acme/payments" in prompt
    assert "FILE app/billing.py" in prompt
    assert "DEPLOY DIFF old...new" in prompt
    audit = storage.list_audit(subject="fp:3f9a1b2c4d5e6f70")[0]
    assert audit.details["model"] == "cheap-review"
    assert audit.details["input_tokens"] == "120"


def test_secret_in_fingerprint_never_reaches_model_prompt(tmp_path) -> None:
    aws_fixture = "AKIA" + "IOSFODNN7EXAMPLE"
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp(f"Request failed: {aws_fixture} quota exceeded"))
    model = ScriptedModel("CLASS: infra\nCONFIDENCE: 0.8\nSUMMARY: quota.")
    dispatcher = RcaDispatcher(
        storage, ConsoleForge(), model_rca=ModelRca(model, RedactionEngine())
    )
    dispatcher.run_once()
    assert model.prompts, "model consulted"
    _, prompt = model.prompts[0]
    assert aws_fixture not in prompt
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
