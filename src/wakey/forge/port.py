# SPDX-License-Identifier: Apache-2.0
"""ForgePort (WF-15): the only way core touches a forge.

Adapters implement this protocol; core never imports a forge SDK. The
console adapter is the development sink — tickets print and are kept in
memory so tests and the two-service demo can assert on them.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from wakey.core.models import Fingerprint


class TicketRef(BaseModel):
    """Pointer to a created ticket on any forge."""

    model_config = ConfigDict(frozen=True)

    issue_id: str = Field(min_length=1)
    url: str = Field(min_length=1)


class ForgePort(Protocol):
    """What every forge adapter must provide (WF-15).

    Ticket surface (M0), branch publication (R-06: the tested tree must be
    *committed*, not pasted into a PR body), and lifecycle effects (R-09:
    close/reopen drive the verification state machine). Wakey never merges
    and never force-pushes — adapters enforce both.
    """

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef: ...

    def add_comment(self, ticket: TicketRef, body: str) -> None: ...

    def open_draft_proposal(self, branch: str, title: str, body: str, base: str) -> TicketRef: ...

    def default_branch(self) -> str: ...

    def create_branch(self, branch: str, base: str) -> None: ...

    def commit_files(self, branch: str, message: str, files: dict[str, str]) -> str:
        """Commit full file contents onto ``branch``; returns the commit sha."""
        ...

    def close_ticket(self, ticket: TicketRef, comment: str | None = None) -> None: ...

    def reopen_ticket(self, ticket: TicketRef, comment: str | None = None) -> None: ...

    def add_label(self, ticket: TicketRef, label: str) -> None: ...

    def fetch_file(self, path: str) -> str | None:
        """Fetch a text file from the repo root (config hot-reload, E3-T6)."""
        ...


class ConsoleForge:
    """Development sink: prints tickets, records every call for assertions."""

    def __init__(self) -> None:
        self.created: list[tuple[Fingerprint, str]] = []
        self.comments: list[tuple[TicketRef, str]] = []
        self.proposals: list[dict[str, str]] = []
        self.branches: dict[str, dict[str, str]] = {}  # branch -> {path: content}
        self.closed: list[TicketRef] = []
        self.reopened: list[TicketRef] = []
        self.labels: list[tuple[TicketRef, str]] = []
        self.fetched: list[str] = []
        self.files: dict[str, str] = {}  # preloaded file contents for fetch_file

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
        self.created.append((fingerprint, body))
        print(f"\n[wakey ticket] {title}\n{body}\n")
        return TicketRef(
            issue_id=f"console-{fingerprint.fp_hash}",
            url=f"console://tickets/{fingerprint.fp_hash}",
        )

    def add_comment(self, ticket: TicketRef, body: str) -> None:
        self.comments.append((ticket, body))
        print(f"[wakey comment on {ticket.issue_id}] {body}")

    def open_draft_proposal(self, branch: str, title: str, body: str, base: str) -> TicketRef:
        self.proposals.append({"branch": branch, "title": title, "body": body, "base": base})
        print(f"[wakey draft proposal] {branch} -> {base}: {title}")
        return TicketRef(issue_id=f"console-{branch}", url=f"console://pulls/{branch}")

    def default_branch(self) -> str:
        return "main"

    def create_branch(self, branch: str, base: str) -> None:
        self.branches[branch] = {}
        print(f"[wakey branch] {branch} from {base}")

    def commit_files(self, branch: str, message: str, files: dict[str, str]) -> str:
        self.branches.setdefault(branch, {}).update(files)
        print(f"[wakey commit] {len(files)} file(s) on {branch}: {message}")
        return f"console-sha-{branch}"

    def close_ticket(self, ticket: TicketRef, comment: str | None = None) -> None:
        self.closed.append(ticket)

    def reopen_ticket(self, ticket: TicketRef, comment: str | None = None) -> None:
        self.reopened.append(ticket)

    def add_label(self, ticket: TicketRef, label: str) -> None:
        self.labels.append((ticket, label))

    def fetch_file(self, path: str) -> str | None:
        self.fetched.append(path)
        return self.files.get(path)
