# SPDX-License-Identifier: Apache-2.0
"""Contract tests: GitHub adapter against recorded-shape responses (G-gate fixture layer)."""

from __future__ import annotations

import httpx
import pytest

from wakey.core.models import Fingerprint, Severity
from wakey.forge.github import (
    ForgeAuthError,
    ForgeError,
    ForgeRateLimited,
    GitHubAdapter,
    GitHubConfig,
)
from wakey.forge.port import ConsoleForge, TicketRef

FP = Fingerprint(
    fp_hash="3f9a1b2c4d5e6f70",
    service="payments-api",
    environment="prod",
    template="Connection refused to db:{num}",
    severity=Severity.ERROR,
    occurrences=3,
)

TOKEN_HEADER = "Bearer ght_test"


def make_adapter(handler: httpx.Handler) -> GitHubAdapter:
    transport = httpx.MockTransport(handler)
    config = GitHubConfig(token="ght_test", repo="acme/payments")
    return GitHubAdapter(config, client=httpx.Client(transport=transport))


def test_create_ticket_posts_payload_and_parses_ref() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["path"] = request.url.path
        seen["json"] = request.read()
        return httpx.Response(
            201, json={"number": 482, "html_url": "https://github.com/acme/payments/issues/482"}
        )

    ref = make_adapter(handler).create_ticket(FP, "[wakey] boom", "body text")
    assert ref.issue_id == "482"
    assert ref.url.endswith("/issues/482")
    assert seen["auth"] == TOKEN_HEADER
    assert seen["path"] == "/repos/acme/payments/issues"
    assert b"wakey" in seen["json"]  # label attached


def test_add_comment_posts_to_issue() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/payments/issues/482/comments"
        return httpx.Response(201, json={"id": 1})

    adapter = make_adapter(handler)
    ref = TicketRef(issue_id="482", url="x")
    adapter.add_comment(ref, "still occurring")


def test_auth_failure_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(ForgeAuthError):
        make_adapter(handler).create_ticket(FP, "t", "b")


def test_rate_limited_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}, json={})

    with pytest.raises(ForgeRateLimited):
        make_adapter(handler).create_ticket(FP, "t", "b")


def test_server_error_retried_then_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={})

    adapter = make_adapter(handler)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(ForgeError):
        adapter.create_ticket(FP, "t", "b")
    assert calls["n"] == 3  # initial + 2 retries, bounded


def test_console_forge_records_for_assertions() -> None:
    forge = ConsoleForge()
    ref = forge.create_ticket(FP, "title", "body")
    forge.add_comment(ref, "note")
    assert len(forge.created) == 1 and len(forge.comments) == 1


def test_open_draft_proposal_posts_draft_pull() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = request.read()
        return httpx.Response(
            201, json={"number": 7, "html_url": "https://github.com/acme/payments/pull/7"}
        )

    config = GitHubConfig(token="ght_test", repo="acme/payments")
    adapter = GitHubAdapter(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    ref = adapter.open_draft_proposal("wakey/fix-fp3f9a", "[wakey] fix (draft)", "body")
    assert ref.issue_id == "7" and ref.url.endswith("/pull/7")
    assert seen["path"] == "/repos/acme/payments/pulls"
    assert b'"draft":true' in seen["json"]
