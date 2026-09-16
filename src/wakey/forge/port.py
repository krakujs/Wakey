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
    """What every forge adapter must provide (minimal M0 surface)."""

    def create_ticket(self, fingerprint: Fingerprint, title: str, body: str) -> TicketRef: ...

    def add_comment(self, ticket: TicketRef, body: str) -> None: ...

    def open_draft_proposal(self, branch: str, title: str, body: str) -> TicketRef: ...


class ConsoleForge:
    """Development sink: prints tickets, records every call for assertions."""

    def __init__(self) -> None:
        self.created: list[tuple[Fingerprint, str]] = []
        self.comments: list[tuple[TicketRef, str]] = []
        self.proposals: list[dict[str, str]] = []

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

    def open_draft_proposal(self, branch: str, title: str, body: str) -> TicketRef:
        self.proposals.append({"branch": branch, "title": title, "body": body})
        print(f"[wakey draft proposal] {branch}: {title}")
        return TicketRef(issue_id=f"console-{branch}", url=f"console://pulls/{branch}")
