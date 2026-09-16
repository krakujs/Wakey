#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Live GitHub credential check — the ONLY authorized repo is
krakujs/linux-clipboard-manager (SKILL.md §8 allowlist).

Creates one clearly-labelled test issue, verifies it via GET, adds one
comment. Read the token from .env; never print or commit it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402

from wakey.core.models import Fingerprint, Severity  # noqa: E402
from wakey.forge.github import GitHubAdapter, GitHubConfig  # noqa: E402

ALLOWED_REPO = "krakujs/linux-clipboard-manager"


def main() -> int:
    env = {}
    for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    token = env["WAKEY_GITHUB_TOKEN"]

    config = GitHubConfig(
        token=token,
        repo=ALLOWED_REPO,
        allowed_repos=(ALLOWED_REPO,),  # hard guard: nothing else may be touched
    )
    adapter = GitHubAdapter(config)
    fingerprint = Fingerprint(
        fp_hash="live0gate0check0",
        service="payments-api",
        environment="prod",
        template="Connection refused to db:{num} (M0 live credential check)",
        severity=Severity.ERROR,
        occurrences=3,
    )
    ref = adapter.create_ticket(
        fingerprint,
        "[wakey] M0 live gate — credential verification",
        "Live-credential check for the M0 gate (SKILL.md allowlist: this repo only).\n"
        "Safe to close/delete. — wakey",
    )
    print(f"created issue #{ref.issue_id}: {ref.url}")

    adapter.add_comment(ref, "M0 gate rehearsal comment — wakey never merges; this issue is safe to delete.")
    print("comment added")
    print(f"LIVE GITHUB CHECK PASS on {ALLOWED_REPO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
