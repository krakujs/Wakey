# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the fingerprint detail page and manual fix trigger (WF-12 §8)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from wakey.core.config import Autonomy, ServiceYaml, Settings
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import AuditEvent, Fingerprint, LogEvent, Service, WorkState
from wakey.core.server import create_app
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.web.board import render_board_html
from wakey.web.fix_trigger import EXECUTOR_NOTE, request_manual_fix

FP_HASH = "fp3f9ac2deadbeef"


def make_client(storage: SQLiteStorage) -> TestClient:
    app = create_app(Settings(), storage, MetricsRegistry(), forge=ConsoleForge())
    return TestClient(app, follow_redirects=False)


def login(client: TestClient) -> None:
    token, _ = client.app.state.auth.ensure_setup_token()  # type: ignore[attr-defined]
    assert client.post("/login", data={"token": token}).status_code == 302


def seed(storage: SQLiteStorage, *, config: ServiceYaml | None = None) -> None:
    storage.save_service(
        Service(
            name="api",
            repo="acme/api",
            ingest_key_hash="abcd1234ef01",
            config_json=config.model_dump_json() if config else "{}",
        )
    )
    storage.save_fingerprint(
        Fingerprint(
            fp_hash=FP_HASH,
            service="api",
            environment="prod",
            template="KeyError: quota",
            occurrences=3,
            state=WorkState.OPEN,
        )
    )


def make_event() -> LogEvent:
    return LogEvent(
        id="delivery1-event0",
        ts=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
        service="api",
        environment="prod",
        severity="error",
        message="KeyError: quota exceeded for tenant 42",
        source="webhook",
    )


def test_detail_page_renders_evidence() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    storage.save_log_event(make_event())
    storage.record_audit(
        AuditEvent(
            actor="agent",
            action="rca.classify",
            subject=f"fp:{FP_HASH}",
            details={"class": "infra", "confidence": "0.85", "summary": "dependency down"},
        )
    )
    storage.record_audit(
        AuditEvent(
            actor="system",
            action="ticket.create",
            subject=f"fp:{FP_HASH}",
            details={"forge_issue": "console-x", "reason": "reached 3 occurrences"},
        )
    )
    client = make_client(storage)
    login(client)
    response = client.get(f"/fingerprints/{FP_HASH}")

    assert response.status_code == 200
    html = response.text
    assert "KeyError: quota" in html  # template headline
    assert "KeyError: quota exceeded" in html  # stored redacted event
    assert "infra" in html and "0.85" in html  # RCA verdict
    assert "dependency down" in html  # RCA summary
    assert "ticket.create" in html  # audit trail


def test_detail_unknown_fingerprint_is_404() -> None:
    client = make_client(SQLiteStorage(":memory:"))
    login(client)
    assert client.get("/fingerprints/nope1234").status_code == 404


def test_anonymous_detail_redirects_to_login() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    client = make_client(storage)
    response = client.get(f"/fingerprints/{FP_HASH}")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_board_rows_link_to_detail() -> None:
    fp = Fingerprint(
        fp_hash=FP_HASH,
        service="api",
        environment="prod",
        template="boom",
        state=WorkState.NEW,
    )
    html = render_board_html([fp], generated_at="2026-09-16T00:00:00+00:00")
    assert f'href="/fingerprints/{FP_HASH}"' in html


def test_fix_request_refusal_names_the_gate_reason() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)  # no RCA audit, no frames → heuristic needs-human → gate refuses class
    outcome = request_manual_fix(storage, FP_HASH)

    assert not outcome.accepted
    assert "not code-fix" in outcome.message
    audits = storage.list_audit(subject=f"fp:{FP_HASH}")
    assert audits[0].action == "fix.requested"
    assert audits[0].actor == "user:dashboard"
    assert audits[0].details["result"].startswith("refused:")


def test_fix_request_blocked_at_executor_when_fully_eligible() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage, config=ServiceYaml(autonomy=Autonomy.FIX))
    storage.record_audit(
        AuditEvent(
            actor="agent",
            action="rca.classify",
            subject=f"fp:{FP_HASH}",
            details={"class": "code-fix", "confidence": "0.9", "summary": "clear"},
        )
    )
    outcome = request_manual_fix(storage, FP_HASH)

    assert not outcome.accepted
    assert outcome.message == EXECUTOR_NOTE
    assert storage.list_audit(subject=f"fp:{FP_HASH}")[0].details["result"] == "blocked-executor"


def test_fix_endpoint_requires_admin_session() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    client = make_client(storage)
    response = client.post(f"/fingerprints/{FP_HASH}/fix")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_fix_endpoint_rejects_cross_origin_post() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    client = make_client(storage)
    login(client)
    response = client.post(
        f"/fingerprints/{FP_HASH}/fix", headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403


def test_fix_endpoint_redirects_with_flash_and_audits() -> None:
    storage = SQLiteStorage(":memory:")
    seed(storage)
    client = make_client(storage)
    login(client)
    response = client.post(f"/fingerprints/{FP_HASH}/fix", headers={"Origin": "http://testserver"})
    assert response.status_code == 303
    assert "flash=" in response.headers["location"]
    followed = client.get(response.headers["location"])
    assert followed.status_code == 200
    assert "not code-fix" in followed.text  # refusal surfaced in the flash
