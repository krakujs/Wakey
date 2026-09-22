# SPDX-License-Identifier: Apache-2.0
"""Digest batching and explicit candidate approval tests (FIX-9)."""

from __future__ import annotations

from datetime import UTC, datetime

from wakey.agents.digest import DigestManager
from wakey.agents.fix_executor import FixExecutionOutcome
from wakey.core.config import Autonomy, ServiceYaml
from wakey.core.models import AuditEvent, Fingerprint, Service, Severity
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.web.commands import CommandDispatcher


def seed(tmp_path):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    forge = ConsoleForge()
    storage.save_service(Service(name="api", repo="acme/api", ingest_key_hash="0123456789abcdef"))
    fingerprint = Fingerprint(
        fp_hash="abcdef1234567890",
        service="api",
        environment="prod",
        template="TypeError: boom",
        severity=Severity.ERROR,
        occurrences=8,
        ticket_issue_id="42",
        ticket_url="https://example.test/issues/42",
    )
    storage.save_fingerprint(fingerprint)
    storage.record_audit(
        AuditEvent(
            actor="agent",
            action="rca.classify",
            subject=f"fp:{fingerprint.fp_hash}",
            details={"class": "code-fix", "confidence": "0.91", "summary": "guard None"},
        )
    )
    config = ServiceYaml(
        autonomy=Autonomy.DIGEST,
        confidence_floor=0.75,
        test_command="pytest -q",
    )
    delivered: list[str] = []

    def execute(fp: Fingerprint, service_config: ServiceYaml) -> FixExecutionOutcome:
        delivered.append(fp.fp_hash)
        return FixExecutionOutcome("delivered", "draft proposal created", "https://pr/1")

    manager = DigestManager(
        storage,
        forge,
        lambda _service: config,
        execute,
        now=lambda: datetime(2026, 9, 22, 9, tzinfo=UTC),
    )
    return storage, forge, fingerprint, manager, delivered


def test_digest_is_one_issue_per_repo_per_day(tmp_path) -> None:
    storage, forge, fingerprint, manager, _ = seed(tmp_path)

    first = manager.run_once()
    second = manager.run_once()

    assert len(first) == len(second) == 1
    assert first[0].created and not second[0].created
    assert len(forge.created) == 1
    assert f"fp:{fingerprint.fp_hash}" in forge.created[0][1]
    assert "@wakey approve" in forge.created[0][1]
    assert forge.labels[-1][1] == "wakey/digest"
    storage.close()


def test_authorized_digest_approval_releases_exact_candidate_once(tmp_path) -> None:
    storage, forge, fingerprint, manager, delivered = seed(tmp_path)
    digest = manager.run_once()[0]
    dispatcher = CommandDispatcher(
        storage,
        forge,
        None,
        authorized_users=frozenset({"alice"}),
        digest_approver=manager.approve,
    )

    reply = dispatcher.handle_comment(
        "alice", digest.issue_id, f"@wakey approve {fingerprint.fp_hash[:8]}"
    )
    duplicate = dispatcher.handle_comment(
        "alice", digest.issue_id, f"@wakey approve {fingerprint.fp_hash[:8]}"
    )

    assert reply == "draft proposal created"
    assert "already released" in str(duplicate)
    assert delivered == [fingerprint.fp_hash]
    storage.close()


def test_unauthorized_digest_approval_is_ignored(tmp_path) -> None:
    storage, forge, fingerprint, manager, delivered = seed(tmp_path)
    digest = manager.run_once()[0]
    dispatcher = CommandDispatcher(
        storage,
        forge,
        None,
        authorized_users=frozenset({"alice"}),
        digest_approver=manager.approve,
    )

    reply = dispatcher.handle_comment(
        "mallory", digest.issue_id, f"@wakey approve {fingerprint.fp_hash}"
    )

    assert reply is None
    assert delivered == []
    storage.close()
