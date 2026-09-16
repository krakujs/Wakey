# SPDX-License-Identifier: Apache-2.0
"""Command bus tests (WF-07, E3-T3): parse, authorize, dispatch, audit."""

from __future__ import annotations

from pathlib import Path

from wakey.agents.rca import RcaDispatcher
from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Fingerprint, Severity, WorkState
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.web.commands import CommandDispatcher, parse_command


def make_fp(state: WorkState = WorkState.OPEN) -> Fingerprint:
    return Fingerprint(
        fp_hash="3f9a1b2c4d5e6f70",
        service="payments-api",
        environment="prod",
        template="TypeError: boom",
        severity=Severity.ERROR,
        occurrences=7,
        state=state,
        ticket_issue_id="42",
        ticket_url="https://github.sim/acme/payments/issues/42",
    )


def make_dispatcher(tmp_path: Path, authorized: frozenset[str] | None = None):
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_fingerprint(make_fp())
    forge = ConsoleForge()
    metrics = MetricsRegistry()
    dispatcher = CommandDispatcher(
        storage,
        forge,
        RcaDispatcher(storage, forge),
        authorized_users=authorized if authorized is not None else frozenset({"alice"}),
        metrics=metrics,
    )
    return dispatcher, storage, forge, metrics


def test_parse_command_extracts_name_and_argument() -> None:
    parsed = parse_command("looks fine to me\n@wakey explain billing.py:87 please")
    assert parsed is not None
    assert parsed.name == "explain"
    assert parsed.argument == "billing.py:87 please"
    assert parse_command("no command here") is None
    assert parse_command("email me at wakey@example.com") is None


def test_bot_comments_are_ignored(tmp_path: Path) -> None:
    dispatcher, _, _, _ = make_dispatcher(tmp_path)
    assert dispatcher.handle_comment("wakey[bot]", "42", "@wakey drop") is None


def test_non_command_comments_are_ignored(tmp_path: Path) -> None:
    dispatcher, _, _, _ = make_dispatcher(tmp_path)
    assert dispatcher.handle_comment("alice", "42", "LGTM, shipping it") is None


def test_unauthorized_user_gets_spec_refusal(tmp_path: Path) -> None:
    dispatcher, _, _, metrics = make_dispatcher(tmp_path)
    reply = dispatcher.handle_comment("mallory", "42", "@wakey drop")
    assert reply is not None and "collaborators" in reply
    assert metrics.snapshot()["wakey_commands_unauthorized_total"] == 1


def test_drop_closes_and_marks_dropped(tmp_path: Path) -> None:
    dispatcher, storage, forge, _ = make_dispatcher(tmp_path)
    reply = dispatcher.handle_comment("alice", "42", "@wakey drop")
    assert reply is not None and "dropped" in reply
    assert storage.get_fingerprint("3f9a1b2c4d5e6f70").state is WorkState.DROPPED
    assert len(forge.closed) == 1


def test_reopen_revives_dropped_fingerprint(tmp_path: Path) -> None:
    dispatcher, storage, forge, _ = make_dispatcher(tmp_path)
    dispatcher.handle_comment("alice", "42", "@wakey drop")
    reply = dispatcher.handle_comment("alice", "42", "@wakey reopen")
    assert reply is not None and "Reopened" in reply
    assert storage.get_fingerprint("3f9a1b2c4d5e6f70").state is WorkState.REOPENED
    assert len(forge.reopened) == 1


def test_explain_posts_rca_analysis(tmp_path: Path) -> None:
    dispatcher, _, forge, _ = make_dispatcher(tmp_path)
    reply = dispatcher.handle_comment("alice", "42", "@wakey explain")
    assert reply is not None and "Re-analysis" in reply


def test_status_reports_state_and_caps(tmp_path: Path) -> None:
    dispatcher, _, _, _ = make_dispatcher(tmp_path)
    reply = dispatcher.handle_comment("alice", "42", "@wakey status")
    assert reply is not None and "open" in reply and "tickets/h" in reply


def test_fix_refusal_names_missing_capability(tmp_path: Path) -> None:
    dispatcher, _, _, _ = make_dispatcher(tmp_path)
    reply = dispatcher.handle_comment("alice", "42", "@wakey fix")
    assert reply is not None and "workspace" in reply


def test_unknown_command_gets_helpful_reply(tmp_path: Path) -> None:
    dispatcher, _, _, _ = make_dispatcher(tmp_path)
    reply = dispatcher.handle_comment("alice", "42", "@wakey nuke everything")
    assert reply is not None and "Unknown command" in reply


def test_foreign_issues_get_silence(tmp_path: Path) -> None:
    dispatcher, _, forge, _ = make_dispatcher(tmp_path)
    assert dispatcher.handle_comment("alice", "999", "@wakey drop") is None
    assert forge.closed == []
