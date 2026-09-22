# SPDX-License-Identifier: Apache-2.0
"""Independent fix-verifier tests (FIX-6)."""

from __future__ import annotations

from wakey.agents.models import ModelReply
from wakey.agents.verifier import ModelVerifier


class ReplyModel:
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompt = ""

    def complete(self, system: str, prompt: str) -> ModelReply:
        self.prompt = prompt
        return ModelReply(text=self.text, data={"input_tokens": "12"})


def test_verifier_passes_justified_diff() -> None:
    model = ReplyModel('{"verdict":"pass","reason":"change matches the RCA"}')
    verdict = ModelVerifier(model).verify("null dereference", "+ guard None", "repro green")
    assert verdict.passed
    assert verdict.reason == "change matches the RCA"
    assert "RCA DATA" in model.prompt


def test_verifier_blocks_injection_driven_change() -> None:
    model = ReplyModel('{"verdict":"block","reason":"diff follows a command embedded in log data"}')
    verdict = ModelVerifier(model).verify(
        "log said ignore policy", "+ upload environment variables", "repro green"
    )
    assert not verdict.passed
    assert "embedded" in verdict.reason


def test_verifier_fails_closed_on_malformed_output() -> None:
    verdict = ModelVerifier(ReplyModel("looks fine")).verify("rca", "diff", "green")
    assert not verdict.passed
    assert "valid verdict" in verdict.reason
