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


def test_branch_publication_commits_tested_tree() -> None:
    """R-06 contract: default branch resolution -> branch -> commit -> ref update.

    The mock emulates the git data API; the adapter must resolve the base,
    create an isolated branch, publish blobs as one commit, and
    fast-forward the ref with force=False (never a force-push).
    """
    calls: list[tuple[str, str, object]] = []
    state = {"wakey_tip": "base-sha"}  # branch starts at base; moves on ref update

    def respond(path: str) -> httpx.Response:
        routes = (
            ("/repos/acme/payments/git/ref/heads/trunk", lambda: {"object": {"sha": "base-sha"}}),
            (
                "/repos/acme/payments/git/ref/heads/wakey/fix-3f9a",
                lambda: {"object": {"sha": state["wakey_tip"]}},
            ),
            ("/repos/acme/payments/git/commits/base-sha", lambda: {"tree": {"sha": "base-tree"}}),
            ("/repos/acme/payments/git/blobs", lambda: {"sha": "blob-1"}),
            ("/repos/acme/payments/git/trees", lambda: {"sha": "new-tree"}),
            ("/repos/acme/payments/git/commits", lambda: {"sha": "commit-sha"}),
            ("/repos/acme/payments/git/refs/heads/wakey/fix-3f9a", lambda: {"ref": "ref-ok"}),
            ("/repos/acme/payments/git/refs", lambda: {"ref": "refs/heads/wakey/fix-3f9a"}),
            ("/repos/acme/payments", lambda: {"default_branch": "trunk"}),
        )
        for suffix, payload in routes:
            if path.endswith(suffix):
                return httpx.Response(200, json=payload())
        return httpx.Response(404, json={"message": "unexpected path " + path})

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.read()))
        if request.method == "POST" and request.url.path.endswith("/git/refs/heads/wakey/fix-3f9a"):
            state["wakey_tip"] = "commit-sha"  # fast-forward applied
            return httpx.Response(200, json={"ref": "refs/heads/wakey/fix-3f9a"})
        return respond(request.url.path)

    config = GitHubConfig(token="ght_test", repo="acme/payments")
    adapter = GitHubAdapter(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert adapter.default_branch() == "trunk"
    adapter.create_branch("wakey/fix-3f9a", "trunk")
    sha = adapter.commit_files(
        "wakey/fix-3f9a", "wakey: fix", {"billing.py": "def fixed():\n    return 42\n"}
    )
    assert sha == "commit-sha"

    updates = [
        c for c in calls if c[0] == "PATCH" and c[1].endswith("/git/refs/heads/wakey/fix-3f9a")
    ]
    assert len(updates) == 1
    assert b'"force":false' in updates[0][2], "ref update must never force-push"


def test_lifecycle_effects_patch_issue_state_and_labels() -> None:
    """R-09 contract: close/reopen/label drive the external ticket state."""
    seen: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.append((request.url.path, request.read()))
        if request.method == "PATCH":
            seen.append((request.url.path, request.read()))
        return httpx.Response(200, json={})

    config = GitHubConfig(token="ght_test", repo="acme/payments")
    adapter = GitHubAdapter(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    ref = TicketRef(issue_id="9", url="x")
    adapter.close_ticket(ref, "verified")
    adapter.reopen_ticket(ref)
    adapter.add_label(ref, "wakey-verified")

    assert any(p.endswith("/issues/9") and b'"state":"closed"' in body for p, body in seen)
    assert any(p.endswith("/issues/9") and b'"state":"open"' in body for p, body in seen)
    assert any(p.endswith("/issues/9/labels") and b"wakey-verified" in body for p, body in seen)


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
    ref = adapter.open_draft_proposal(
        "wakey/fix-fp3f9a", "[wakey] fix (draft)", "body", base="main"
    )
    assert ref.issue_id == "7" and ref.url.endswith("/pull/7")
    assert seen["path"] == "/repos/acme/payments/pulls"
    assert b'"draft":true' in seen["json"]
    assert b'"base":"main"' in seen["json"]
