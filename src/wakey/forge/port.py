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


class ForgeError(Exception):
    """Base for forge API failures after bounded retries.

    ``status_code`` records the HTTP status when the forge answered; a
    transport-level failure leaves it ``None`` because a write may still
    have landed.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ForgeAuthError(ForgeError):
    """Token rejected or permission narrowed (401/403 non-rate)."""


class ForgeRateLimited(ForgeError):
    """Primary or secondary rate limit hit; backoff was exhausted."""


class ForgeUnreachable(ForgeError):
    """Transport failure: whether the forge accepted a write is unknown."""


def is_ambiguous_write(exc: Exception) -> bool:
    """True when ``exc`` leaves whether the forge accepted a write unknown (R-15).

    Definitive rejections (auth, rate limit, 4xx) wrote nothing and may be
    retried directly; transport losses and 5xx-after-write did not answer
    that question, so the caller must reconcile against the remote before
    creating a second object. Exceptions from outside the adapter layer
    (programming bugs) are treated as definitive — the pre-write behavior.
    """
    if isinstance(exc, ForgeUnreachable):
        return True
    if isinstance(exc, (ForgeAuthError, ForgeRateLimited)):
        return False
    if isinstance(exc, ForgeError):
        return exc.status_code is None or exc.status_code >= 500
    return False


class ForgePort(Protocol):
    """What every forge adapter must provide (WF-15).

    Ticket surface (M0), branch publication (R-06: the tested tree must be
    *committed*, not pasted into a PR body), and lifecycle effects (R-09:
    close/reopen drive the verification state machine). Wakey never merges
    and never force-pushes — adapters enforce both.
    """

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef: ...

    def find_ticket_by_marker(self, marker: str) -> TicketRef | None:
        """Return the open ticket whose body carries ``marker`` (``fp:<hash>``).

        External-write reconciliation (R-15): when a create call fails
        ambiguously (lost response after GitHub accepted it), the caller
        looks the intended object up before creating a second one.
        """
        ...

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

    def compare_commits(self, base: str, head: str) -> str | None:
        """Bounded textual deploy diff, or ``None`` when unavailable."""
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
        self.comparisons: dict[tuple[str, str], str] = {}

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef:
        self.created.append((fingerprint, body))
        print(f"\n[wakey ticket] {title}\n{body}\n")
        return TicketRef(
            issue_id=f"console-{fingerprint.fp_hash}",
            url=f"console://tickets/{fingerprint.fp_hash}",
        )

    def find_ticket_by_marker(self, marker: str) -> TicketRef | None:
        stem = marker.removeprefix("fp:")
        return next(
            (
                TicketRef(issue_id=f"console-{fp.fp_hash}", url=f"console://tickets/{fp.fp_hash}")
                for fp, _ in reversed(self.created)
                if fp.fp_hash == stem
            ),
            None,
        )

    def add_comment(self, ticket: TicketRef, body: str) -> None:
        self.comments.append((ticket, body))
        print(f"[wakey comment on {ticket.issue_id}] {body}")

    def open_draft_proposal(self, branch: str, title: str, body: str, base: str) -> TicketRef:
        # one proposal per branch: a retried delivery binds to the existing
        # draft instead of opening a second one (R-15)
        existing = next(
            (p for p in self.proposals if p["branch"] == branch),
            None,
        )
        if existing is not None:
            return TicketRef(issue_id=f"console-{branch}", url=f"console://pulls/{branch}")
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

    def compare_commits(self, base: str, head: str) -> str | None:
        return self.comparisons.get((base, head))
