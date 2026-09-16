# SPDX-License-Identifier: Apache-2.0
"""Wakey domain models (E2-T1).

These are the vocabulary every other module speaks. They are pure data —
no I/O, no config — so ingest, detection, tickets, and agents can be tested
in isolation. Timestamps are always timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Current UTC time (timezone-aware); free function so tests can patch it."""
    return datetime.now(UTC)


class Severity(StrEnum):
    """Normalized log severity; a fingerprint's severity is the max seen."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Ordering rank so callers can compare severities without knowing order."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.DEBUG: 0,
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.ERROR: 3,
    Severity.CRITICAL: 4,
}


class WorkState(StrEnum):
    """Lifecycle of a fingerprint's work item (WF-04 §0, DET-14).

    Exactly one state per fingerprint; dispatches are only legal from the
    non-busy states (policy enforces the transitions, see WF-04 rules).
    """

    NEW = "new"
    OPEN = "open"  # observe mode or waiting on policy gate
    QUEUED_RCA = "queued-rca"
    INVESTIGATING = "investigating"
    AWAITING_HUMAN = "awaiting-human"
    FIXING = "fixing"
    VERIFYING = "verifying"
    VERIFIED_CLOSED = "verified-closed"
    CLOSED_AUTO = "closed-auto"
    CLOSED_HUMAN = "closed-human"
    DROPPED = "dropped"
    REOPENED = "reopened"
    ARCHIVED = "archived"

    @property
    def busy(self) -> bool:
        """Busy states absorb occurrences without spawning parallel work (DET-14)."""
        return self in _BUSY_STATES


_BUSY_STATES = frozenset(
    {
        WorkState.QUEUED_RCA,
        WorkState.INVESTIGATING,
        WorkState.FIXING,
        WorkState.VERIFYING,
    }
)


class TraceFrame(BaseModel):
    """One normalized stack frame: where the error passed through."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    line: int = Field(ge=0)
    function: str = Field(min_length=1)


class LogEvent(BaseModel):
    """A single redacted, normalized log event as stored and queued (WF-02 §4-5)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=8)  # delivery-scoped unique id (idempotency)
    ts: datetime
    service: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    severity: Severity
    message: str = Field(min_length=1)
    source: str = Field(min_length=1)  # e.g. "gcp-pubsub", "webhook", "docker"
    frames: tuple[TraceFrame, ...] = ()
    trace_id: str | None = None
    request_id: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class Fingerprint(BaseModel):
    """A deduplicated error family: the unit Wakey wakes up for (WF-03 §4)."""

    fp_hash: str = Field(min_length=8)
    service: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    template: str = Field(min_length=1)
    frames: tuple[TraceFrame, ...] = ()
    severity: Severity = Severity.ERROR
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    occurrences: int = Field(default=0, ge=0)
    state: WorkState = WorkState.NEW
    ticket_issue_id: str | None = None
    ticket_url: str | None = None
    # DB-truth throttles and lifecycle anchors (R-05/R-09):
    commented_at_occurrences: int = Field(default=0, ge=0)
    last_comment_at: datetime | None = None
    verification_started_at: datetime | None = None
    verification_start_occurrences: int = Field(default=0, ge=0)
    chronic: bool = False
    proposal_issue_id: str | None = None
    proposal_url: str | None = None
    proposal_branch: str | None = None


class DeliveryState(StrEnum):
    """Durable ingest delivery states (WF-02 §7, R-02).

    A delivery is ``pending`` until its events are fully persisted and
    dispatched; a crash between those points leaves it recoverable.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"  # dead-lettered: attempts exhausted


class Delivery(BaseModel):
    """One accepted ingest payload awaiting durable processing (R-02)."""

    service: str = Field(min_length=1)
    delivery_id: str = Field(min_length=1)
    state: DeliveryState = DeliveryState.PENDING
    payload: str = ""  # redacted raw body; replayable after restart
    attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DeployEvent(BaseModel):
    """One recorded deployment of a service (WF-08 deploy-aware verification)."""

    service: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    sha: str = Field(min_length=1)
    deployed_at: datetime = Field(default_factory=utcnow)


class Service(BaseModel):
    """A registered service: one deployable bound to a repo (WF-01 §A3)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    repo: str = Field(min_length=3, pattern=r"[\w.-]+/[\w.-]+")  # owner/name
    path: str = "."  # monorepo path scope (FIX-3 enforcement root)
    forge: str = "github"
    environment: str = "prod"
    ingest_key_hash: str = Field(min_length=8)
    webhook_secret: str | None = None
    config_json: str = "{}"  # canonical ServiceYaml persisted at registration (E3-T6)


class AuditEvent(BaseModel):
    """One line of the append-only audit log (SEC-5): who did what, when."""

    ts: datetime = Field(default_factory=utcnow)
    actor: str = Field(min_length=1)  # "system" | "agent" | "user:<login>"
    action: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    details: dict[str, str] = Field(default_factory=dict)
