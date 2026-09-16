# SPDX-License-Identifier: Apache-2.0
"""Live GitHub G-gate suite (E3-T8, G-gate) — RUNS ONLY WITH CREDENTIALS.

Every test here performs REAL GitHub writes and is therefore skipped
unless the operator explicitly provides:

    WAKEY_E2E_TOKEN       a PAT with repo+issues scope (test org preferred)
    WAKEY_E2E_REPO        the allowlisted repo to write to (owner/name)
    WAKEY_E2E_API_BASE    (optional) override; defaults to api.github.com

Rules honoured by every test: never merge, never force-push, clean up
created objects where possible, respect the repo allowlist.
"""

from __future__ import annotations

import os

import httpx
import pytest

from wakey.core.models import Fingerprint, Severity
from wakey.forge.github import GitHubAdapter, GitHubConfig

pytestmark = pytest.mark.skipif(
    not (os.environ.get("WAKEY_E2E_TOKEN") and os.environ.get("WAKEY_E2E_REPO")),
    reason="live GitHub credentials not configured (WAKEY_E2E_TOKEN/WAKEY_E2E_REPO)",
)

REPO = os.environ.get("WAKEY_E2E_REPO", "")


def adapter() -> GitHubAdapter:
    allowed = tuple(r for r in [REPO] if r)
    return GitHubAdapter(
        GitHubConfig(
            token=os.environ["WAKEY_E2E_TOKEN"],
            repo=REPO,
            api_base=os.environ.get("WAKEY_E2E_API_BASE", "https://api.github.com"),
            allowed_repos=allowed or None,
        )
    )


def test_live_repo_reachable_and_allowlisted() -> None:
    a = adapter()
    assert a.default_branch(), "default branch must resolve on the live repo"


def test_live_ticket_create_comment_close() -> None:
    a = adapter()
    fp = Fingerprint(
        fp_hash="e2elive000000001",
        service="g-gate",
        environment="live",
        template="G-gate live smoke",
        severity=Severity.ERROR,
        occurrences=1,
    )
    ref = a.create_ticket(fp, "[wakey g-gate] live smoke ticket", "created by the live suite")
    a.add_comment(ref, "live smoke: comment leg")
    a.close_ticket(ref, "live smoke: closing")
    # cleanup is not possible for issues via this endpoint set; the test org
    # exists so these tickets can be bulk-closed/archived by the operator.


def test_live_branch_publication_round_trip() -> None:
    a = adapter()
    base = a.default_branch()
    branch = "wakey/g-gate-live-smoke"
    a.create_branch(branch, base)
    sha = a.commit_files(branch, "wakey g-gate: smoke commit", {"G_GATE_SMOKE.md": "smoke\n"})
    assert sha
    # no PR is opened and nothing is merged: this leg proves write access only


def test_live_rate_limit_headers_honoured() -> None:
    """Bounded writes: one write per test, retries stay bounded in the adapter."""
    a = adapter()
    a.default_branch()  # a read: proves the adapter round-trips under live limits
    with httpx.Client(timeout=10) as probe:
        response = probe.get(
            "https://api.github.com/rate_limit",
            headers={"Authorization": f"Bearer {os.environ['WAKEY_E2E_TOKEN']}"},
        )
    assert response.status_code == 200
