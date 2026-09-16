# SPDX-License-Identifier: Apache-2.0
"""RCA agent tests: heuristic classification + comment posting (M1 skeleton)."""

from __future__ import annotations

from wakey.agents.rca import RcaAgent, classify
from wakey.core.models import Fingerprint, Severity, TraceFrame
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge


def make_fp(template, with_frames=False, severity=Severity.ERROR):
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template=template,
        frames=(TraceFrame(path="app/billing.py", line=87, function="charge"),)
        if with_frames
        else (),
        severity=severity,
        ticket_issue_id="console-3f9a",
        ticket_url="console://tickets/3f9a",
    )


def test_infra_pattern():
    result = classify(make_fp("Connection refused to payments-db:{num}"))
    assert result.classification == "infra"


def test_config_pattern():
    result = classify(make_fp("ImportError: No module named 'billing'"))
    assert result.classification == "config"


def test_code_fix_with_frames():
    result = classify(make_fp("TypeError: 'NoneType' object is not subscriptable", True))
    assert result.classification == "code-fix"
    assert result.confidence >= 0.6


def test_needs_human_without_evidence():
    result = classify(make_fp("something unusual happened"))
    assert result.classification == "needs-human"


def test_agent_posts_rca_comment_and_audits(tmp_path):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    result = RcaAgent(storage, forge).investigate(make_fp("TypeError: boom", True))
    assert result.classification == "code-fix"
    assert len(forge.comments) == 1
    assert "confidence" in forge.comments[0][1]
