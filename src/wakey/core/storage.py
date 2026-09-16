# SPDX-License-Identifier: Apache-2.0
"""Storage: abstract interface + SQLite implementation (E2-T3).

The rest of Wakey depends on :class:`Storage`, never on SQLite directly —
the Postgres path (OPS-5) and the in-memory test fake implement the same
interface. Concurrency model: one connection guarded by a lock (server
endpoints run in FastAPI's threadpool); queue workers (E2-T5) serialize
through the same instance.

Durability contract (R-02): an accepted delivery is persisted *before* the
HTTP request answers, and processed exactly once via event-level idempotency
— a crash at any point leaves the delivery recoverable (pending → claimed →
completed), and a replay never duplicates tickets or loses events.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wakey.core.models import (
    AuditEvent,
    Delivery,
    DeliveryState,
    DeployEvent,
    Fingerprint,
    LogEvent,
    Service,
    Severity,
    TraceFrame,
    WorkState,
)
from wakey.security.secretbox import SecretBox

SCHEMA_VERSION = 5

_MIGRATION_V2 = "ALTER TABLE services ADD COLUMN webhook_secret TEXT"

_MIGRATION_V3 = """
ALTER TABLE services ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE fingerprints ADD COLUMN commented_at_occurrences INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fingerprints ADD COLUMN last_comment_at TEXT;
ALTER TABLE fingerprints ADD COLUMN verification_started_at TEXT;
ALTER TABLE fingerprints ADD COLUMN verification_start_occurrences INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fingerprints ADD COLUMN chronic INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fingerprints ADD COLUMN proposal_issue_id TEXT;
ALTER TABLE fingerprints ADD COLUMN proposal_url TEXT;
ALTER TABLE fingerprints ADD COLUMN proposal_branch TEXT;
ALTER TABLE log_events ADD COLUMN dispatched INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS deliveries (
    service TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    state TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (service, delivery_id)
);
"""

_MIGRATION_V4 = """
CREATE TABLE IF NOT EXISTS secrets (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    sha TEXT NOT NULL,
    deployed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deploys_service_ts ON deployments (service, deployed_at);
"""

_MIGRATION_V5 = """
ALTER TABLE audit ADD COLUMN prev_hash TEXT;
ALTER TABLE audit ADD COLUMN entry_hash TEXT;
"""

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
    name TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    forge TEXT NOT NULL,
    environment TEXT NOT NULL,
    ingest_key_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS log_events (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL,
    trace_id TEXT,
    request_id TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS fingerprints (
    fp_hash TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    template TEXT NOT NULL,
    frames_json TEXT NOT NULL DEFAULT '[]',
    severity TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    ticket_issue_id TEXT,
    ticket_url TEXT
);
CREATE TABLE IF NOT EXISTS delivery_ids (
    delivery_id TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_service_ts ON log_events (service, ts);
CREATE INDEX IF NOT EXISTS idx_fp_service_state ON fingerprints (service, state);
"""


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Storage(ABC):
    """Persistence contract for all Wakey state (implemented per backend)."""

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def check_ready(self) -> bool:
        """Cheap liveness probe for /readyz (WF-11 §2)."""

    # --- durable deliveries (R-02) -------------------------------------------

    @abstractmethod
    def save_delivery(self, delivery: Delivery) -> bool:
        """Persist a pending delivery atomically.

        True if this (service, delivery_id) pair was seen for the first
        time; False means a replay — regardless of the stored state, the
        delivery is (or was) handled and the caller must not reprocess.
        """

    @abstractmethod
    def claim_next_delivery(self) -> Delivery | None:
        """Atomically move the oldest pending delivery to ``processing``."""

    @abstractmethod
    def complete_delivery(self, service: str, delivery_id: str) -> None:
        """Mark a fully processed delivery ``completed``."""

    @abstractmethod
    def fail_delivery(self, service: str, delivery_id: str, max_attempts: int) -> DeliveryState:
        """Record a processing failure.

        Attempts increment; below ``max_attempts`` the delivery returns to
        ``pending`` for retry, at the cap it is dead-lettered ``failed``.
        """

    @abstractmethod
    def recover_stale_deliveries(self) -> int:
        """Reset ``processing`` deliveries to ``pending`` (crash recovery)."""

    @abstractmethod
    def pending_delivery_count(self) -> int:
        """Deliveries waiting or in flight (readiness/metrics)."""

    # --- services ------------------------------------------------------------

    @abstractmethod
    def save_service(self, service: Service) -> None: ...

    @abstractmethod
    def get_service(self, name: str) -> Service | None: ...

    @abstractmethod
    def get_service_by_ingest_key_hash(self, key_hash: str) -> Service | None: ...

    @abstractmethod
    def has_services(self) -> bool:
        """True when at least one service is registered (readiness state, R-01)."""

    @abstractmethod
    def list_services(self) -> list[Service]:
        """All registered services (wizard/service list, WF-01 §5)."""

    # --- fingerprints ----------------------------------------------------------

    @abstractmethod
    def record_occurrence(self, incoming: Fingerprint, delta: int = 1) -> Fingerprint:
        """Insert or update a fingerprint, absorbing ``delta`` occurrences.

        Counter semantics: stored ``occurrences`` increments by ``delta``;
        stored ``first_seen`` is preserved; every other field is taken from
        ``incoming`` (the caller owns state transitions).
        """

    @abstractmethod
    def get_fingerprint(self, fp_hash: str) -> Fingerprint | None: ...

    @abstractmethod
    def save_fingerprint(self, fingerprint: Fingerprint) -> None:
        """Persist the fingerprint exactly as given (state transitions, ticket refs)."""

    @abstractmethod
    def list_active_fingerprints(self, service: str | None = None) -> list[Fingerprint]: ...

    # --- events & audit ----------------------------------------------------------

    @abstractmethod
    def save_log_event(self, event: LogEvent) -> bool:
        """Persist one event; True if new, False if the id already exists.

        The boolean combines with :meth:`is_event_dispatched` to form the
        exactly-once dispatch gate (R-02): an event is dispatched when it
        is new *or* stored but not yet marked dispatched.
        """

    @abstractmethod
    def is_event_dispatched(self, event_id: str) -> bool: ...

    @abstractmethod
    def mark_event_dispatched(self, event_id: str) -> None:
        """Journal that the pipeline fully handled this event."""

    @abstractmethod
    def record_audit(self, event: AuditEvent) -> None: ...

    @abstractmethod
    def verify_audit_chain(self) -> tuple[bool, int]:
        """Verify the append-only audit hash chain (SEC-5, E10-T4).

        Returns (chain_ok, rows_checked). A tampered or deleted row breaks
        the chain; verification recomputes every link from genesis.
        """

    @abstractmethod
    def list_audit(self, subject: str | None = None, limit: int = 50) -> list[AuditEvent]:
        """Audit lines, newest first, optionally scoped to one subject (WF-12)."""

    @abstractmethod
    def recent_events(
        self, service: str, environment: str | None = None, limit: int = 20
    ) -> list[LogEvent]:
        """Most recent stored (redacted) events, newest first (WF-12 detail page)."""

    # --- secrets, deploy events, issue binding, backup -------------------------

    @abstractmethod
    def get_secret(self, key: str) -> str | None: ...

    @abstractmethod
    def set_secret(self, key: str, value: str) -> None: ...

    @abstractmethod
    def delete_secret(self, key: str) -> None: ...

    @abstractmethod
    def get_fingerprint_by_issue(self, issue_id: str) -> Fingerprint | None:
        """The fingerprint bound to a forge issue (command bus, R-09)."""

    @abstractmethod
    def get_fingerprint_by_branch(self, branch: str) -> Fingerprint | None:
        """The fingerprint bound to a fix-proposal branch (merge binding, WF-08)."""

    @abstractmethod
    def save_deploy(self, event: DeployEvent) -> None: ...

    @abstractmethod
    def list_recent_deploys(self, service: str, limit: int = 10) -> list[DeployEvent]: ...

    @abstractmethod
    def backup(self, dest: Path) -> None:
        """Consistent online backup of the store (OPS-6)."""

    @abstractmethod
    def prune_events(self, older_than: datetime) -> int:
        """Retention (R-14): delete log events older than the cutoff."""

    @abstractmethod
    def prune_deliveries(self, older_than: datetime) -> int:
        """Retention (R-14): delete completed/failed deliveries past cutoff."""


class SQLiteStorage(Storage):
    """Single-file storage behind the Storage interface (NFR-2, OPS-5)."""

    def __init__(self, path: Path | str, secret_box: SecretBox | None = None) -> None:
        self._lock = threading.Lock()
        self._secret_box = secret_box
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._backfill_audit_chain()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            current = int(row[0]) if row else 0
            migrations: dict[int, str] = {
                1: _SCHEMA_V1,
                2: _MIGRATION_V2,
                3: _MIGRATION_V3,
                4: _MIGRATION_V4,
                5: _MIGRATION_V5,
            }
            for version in sorted(migrations):
                if version > current:
                    self._conn.executescript(migrations[version])
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def check_ready(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    # --- durable deliveries (R-02) ----------------------------------------------

    def save_delivery(self, delivery: Delivery) -> bool:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO deliveries (service, delivery_id, state, payload, attempts, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(service, delivery_id) DO NOTHING",
                (
                    delivery.service,
                    delivery.delivery_id,
                    DeliveryState.PENDING.value,
                    delivery.payload,
                    delivery.attempts,
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def claim_next_delivery(self) -> Delivery | None:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM deliveries WHERE state = ? ORDER BY created_at LIMIT 1",
                (DeliveryState.PENDING.value,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE deliveries SET state = ?, updated_at = ? "
                "WHERE service = ? AND delivery_id = ?",
                (DeliveryState.PROCESSING.value, now, row["service"], row["delivery_id"]),
            )
        return self._delivery_from_row(dict(row), state=DeliveryState.PROCESSING)

    def complete_delivery(self, service: str, delivery_id: str) -> None:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE deliveries SET state = ?, updated_at = ? "
                "WHERE service = ? AND delivery_id = ?",
                (DeliveryState.COMPLETED.value, now, service, delivery_id),
            )

    def fail_delivery(self, service: str, delivery_id: str, max_attempts: int) -> DeliveryState:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT attempts FROM deliveries WHERE service = ? AND delivery_id = ?",
                (service, delivery_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"delivery not found: {service}/{delivery_id}")
            attempts = int(row["attempts"]) + 1
            state = DeliveryState.FAILED if attempts >= max_attempts else DeliveryState.PENDING
            self._conn.execute(
                "UPDATE deliveries SET attempts = ?, state = ?, updated_at = ? "
                "WHERE service = ? AND delivery_id = ?",
                (attempts, state.value, now, service, delivery_id),
            )
            return state

    def recover_stale_deliveries(self) -> int:
        """Crash recovery: anything left ``processing`` goes back to ``pending``."""
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE deliveries SET state = ?, updated_at = ? WHERE state = ?",
                (DeliveryState.PENDING.value, now, DeliveryState.PROCESSING.value),
            )
            return cursor.rowcount

    def pending_delivery_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM deliveries WHERE state IN (?, ?)",
                (DeliveryState.PENDING.value, DeliveryState.PROCESSING.value),
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _delivery_from_row(row: dict[str, Any], state: DeliveryState) -> Delivery:
        return Delivery(
            service=str(row["service"]),
            delivery_id=str(row["delivery_id"]),
            state=state,
            payload=str(row["payload"]),
            attempts=int(row["attempts"]),
            created_at=_from_iso(str(row["created_at"])),
            updated_at=_from_iso(str(row["updated_at"])),
        )

    # --- services -----------------------------------------------------------------

    def save_service(self, service: Service) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO services (name, repo, path, forge, environment, ingest_key_hash, "
                "webhook_secret, config_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET repo=excluded.repo, path=excluded.path, "
                "forge=excluded.forge, environment=excluded.environment, "
                "ingest_key_hash=excluded.ingest_key_hash, "
                "webhook_secret=excluded.webhook_secret, config_json=excluded.config_json",
                (
                    service.name,
                    service.repo,
                    service.path,
                    service.forge,
                    service.environment,
                    service.ingest_key_hash,
                    self._encrypt_webhook_secret(service.webhook_secret),
                    service.config_json,
                ),
            )

    def _encrypt_webhook_secret(self, secret: str | None) -> str | None:
        if secret is None or self._secret_box is None:
            return secret
        return self._secret_box.encrypt(secret)

    def _service_from_row(self, row: sqlite3.Row) -> Service:
        columns = set(row.keys())
        webhook_secret = row["webhook_secret"]
        if self._secret_box is not None and webhook_secret is not None:
            webhook_secret = self._secret_box.decrypt(str(webhook_secret))
        return Service(
            name=row["name"],
            repo=row["repo"],
            path=row["path"],
            forge=row["forge"],
            environment=row["environment"],
            ingest_key_hash=row["ingest_key_hash"],
            webhook_secret=webhook_secret,
            config_json=row["config_json"] if "config_json" in columns else "{}",
        )

    def get_service(self, name: str) -> Service | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM services WHERE name = ?", (name,)).fetchone()
        return None if row is None else self._service_from_row(row)

    def get_service_by_ingest_key_hash(self, key_hash: str) -> Service | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM services WHERE ingest_key_hash = ?", (key_hash,)
            ).fetchone()
        return None if row is None else self._service_from_row(row)

    def has_services(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM services LIMIT 1").fetchone()
        return row is not None

    def list_services(self) -> list[Service]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM services ORDER BY name").fetchall()
        return [self._service_from_row(row) for row in rows]

    # --- fingerprints -----------------------------------------------------------

    def record_occurrence(self, incoming: Fingerprint, delta: int = 1) -> Fingerprint:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT occurrences, first_seen, state, ticket_issue_id, ticket_url, "
                "commented_at_occurrences, last_comment_at, verification_started_at, "
                "verification_start_occurrences, chronic, proposal_issue_id, proposal_url, "
                "proposal_branch FROM fingerprints WHERE fp_hash = ?",
                (incoming.fp_hash,),
            ).fetchone()
            if row is None:
                stored = incoming.model_copy(update={"occurrences": incoming.occurrences + delta})
                self._conn.execute(
                    "INSERT INTO fingerprints (fp_hash, service, environment, template, "
                    "frames_json, severity, first_seen, last_seen, occurrences, state, "
                    "ticket_issue_id, ticket_url, commented_at_occurrences, last_comment_at, "
                    "verification_started_at, verification_start_occurrences, chronic, "
                    "proposal_issue_id, proposal_url, proposal_branch) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _fingerprint_params(stored),
                )
                return stored

            merged = incoming.model_copy(
                update={
                    "occurrences": int(row["occurrences"]) + delta,
                    "first_seen": _from_iso(row["first_seen"]),
                    # counters change; lifecycle state, ticket refs and throttle
                    # anchors are DB truth
                    "state": WorkState(row["state"]),
                    "ticket_issue_id": row["ticket_issue_id"],
                    "ticket_url": row["ticket_url"],
                    "commented_at_occurrences": int(row["commented_at_occurrences"]),
                    "last_comment_at": (
                        _from_iso(row["last_comment_at"]) if row["last_comment_at"] else None
                    ),
                    "verification_started_at": (
                        _from_iso(row["verification_started_at"])
                        if row["verification_started_at"]
                        else None
                    ),
                    "verification_start_occurrences": int(row["verification_start_occurrences"]),
                    "chronic": bool(row["chronic"]),
                    "proposal_issue_id": row["proposal_issue_id"],
                    "proposal_url": row["proposal_url"],
                    "proposal_branch": row["proposal_branch"],
                }
            )
            self._conn.execute(
                "UPDATE fingerprints SET template=?, frames_json=?, severity=?, last_seen=?, "
                "occurrences=? WHERE fp_hash=?",
                (
                    merged.template,
                    json.dumps([frame.model_dump() for frame in merged.frames]),
                    merged.severity.value,
                    _iso(merged.last_seen),
                    merged.occurrences,
                    merged.fp_hash,
                ),
            )
            return merged

    def get_fingerprint(self, fp_hash: str) -> Fingerprint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM fingerprints WHERE fp_hash = ?", (fp_hash,)
            ).fetchone()
        if row is None:
            return None
        return _row_to_fingerprint(row)

    def save_fingerprint(self, fingerprint: Fingerprint) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO fingerprints (fp_hash, service, environment, template, "
                "frames_json, severity, first_seen, last_seen, occurrences, state, "
                "ticket_issue_id, ticket_url, commented_at_occurrences, last_comment_at, "
                "verification_started_at, verification_start_occurrences, chronic, "
                "proposal_issue_id, proposal_url, proposal_branch) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(fp_hash) DO UPDATE SET template=excluded.template, "
                "frames_json=excluded.frames_json, severity=excluded.severity, "
                "last_seen=excluded.last_seen, occurrences=excluded.occurrences, "
                "state=excluded.state, ticket_issue_id=excluded.ticket_issue_id, "
                "ticket_url=excluded.ticket_url, "
                "commented_at_occurrences=excluded.commented_at_occurrences, "
                "last_comment_at=excluded.last_comment_at, "
                "verification_started_at=excluded.verification_started_at, "
                "verification_start_occurrences=excluded.verification_start_occurrences, "
                "chronic=excluded.chronic, "
                "proposal_issue_id=excluded.proposal_issue_id, "
                "proposal_url=excluded.proposal_url, "
                "proposal_branch=excluded.proposal_branch",
                _fingerprint_params(fingerprint),
            )

    def list_active_fingerprints(self, service: str | None = None) -> list[Fingerprint]:
        """Fingerprints not closed/archived/dropped — the board's live rows."""
        quiet = (
            WorkState.CLOSED_AUTO,
            WorkState.CLOSED_HUMAN,
            WorkState.DROPPED,
            WorkState.VERIFIED_CLOSED,
            WorkState.ARCHIVED,
        )
        placeholders = ",".join("?" for _ in quiet)
        query = f"SELECT * FROM fingerprints WHERE state NOT IN ({placeholders}) ORDER BY last_seen DESC"  # noqa: E501
        with self._lock:
            rows = self._conn.execute(query, tuple(s.value for s in quiet)).fetchall()
        return [
            _row_to_fingerprint(row)
            for row in rows
            if (service is None or row["service"] == service)
        ]

    # --- events & audit -----------------------------------------------------------

    def save_log_event(self, event: LogEvent) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO log_events (id, ts, service, environment, severity, "
                "message, source, trace_id, request_id, attributes_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    event.id,
                    _iso(event.ts),
                    event.service,
                    event.environment,
                    event.severity.value,
                    event.message,
                    event.source,
                    event.trace_id,
                    event.request_id,
                    json.dumps(event.attributes),
                ),
            )
            return cursor.rowcount == 1

    def is_event_dispatched(self, event_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT dispatched FROM log_events WHERE id = ?", (event_id,)
            ).fetchone()
        return bool(row and row["dispatched"])

    def mark_event_dispatched(self, event_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE log_events SET dispatched = 1 WHERE id = ?", (event_id,))

    @staticmethod
    def _entry_hash(prev_hash: str | None, row_values: tuple[object, ...]) -> str:
        material = "|".join(str(v) for v in (prev_hash or "", *row_values))
        return hashlib.sha256(material.encode()).hexdigest()

    def record_audit(self, event: AuditEvent) -> None:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            prev = self._conn.execute(
                "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev["entry_hash"] if prev else None
            row_values = (now, event.actor, event.action, event.subject, json.dumps(event.details))
            entry_hash = self._entry_hash(prev_hash, row_values)
            self._conn.execute(
                "INSERT INTO audit (ts, actor, action, subject, details_json, "
                "prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    event.actor,
                    event.action,
                    event.subject,
                    json.dumps(event.details),
                    prev_hash,
                    entry_hash,
                ),
            )

    def verify_audit_chain(self) -> tuple[bool, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, ts, actor, action, subject, details_json, "
                "prev_hash, entry_hash FROM audit ORDER BY seq"
            ).fetchall()
        prev: str | None = None
        for row in rows:
            row_values = (
                row["ts"],
                row["actor"],
                row["action"],
                row["subject"],
                row["details_json"],
            )
            expected = self._entry_hash(prev, row_values)
            if row["entry_hash"] != expected or row["prev_hash"] != prev:
                return False, rows.index(row)
            prev = row["entry_hash"]
        return True, len(rows)

    def _backfill_audit_chain(self) -> None:
        """Migration v5 helper: chain legacy rows that predate the hash columns."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT seq, ts, actor, action, subject, details_json, entry_hash "
                "FROM audit WHERE entry_hash IS NULL ORDER BY seq"
            ).fetchall()
            if not rows:
                return
            prev_row = self._conn.execute(
                "SELECT entry_hash FROM audit WHERE entry_hash IS NOT NULL "
                "AND seq < ? ORDER BY seq DESC LIMIT 1",
                (rows[0]["seq"],),
            ).fetchone()
            prev = prev_row["entry_hash"] if prev_row else None
            for row in rows:
                row_values = (
                    row["ts"],
                    row["actor"],
                    row["action"],
                    row["subject"],
                    row["details_json"],
                )
                entry_hash = self._entry_hash(prev, row_values)
                self._conn.execute(
                    "UPDATE audit SET prev_hash = ?, entry_hash = ? WHERE seq = ?",
                    (prev, entry_hash, row["seq"]),
                )
                prev = entry_hash

    def list_audit(self, subject: str | None = None, limit: int = 50) -> list[AuditEvent]:
        sql = "SELECT ts, actor, action, subject, details_json FROM audit"
        params: tuple[object, ...] = ()
        if subject is not None:
            sql += " WHERE subject = ?"
            params = (subject,)
        sql += " ORDER BY seq DESC LIMIT ?"
        rows = self._conn.execute(sql, (*params, limit)).fetchall()
        return [
            AuditEvent(
                ts=_from_iso(row["ts"]),
                actor=row["actor"],
                action=row["action"],
                subject=row["subject"],
                details=json.loads(row["details_json"]),
            )
            for row in rows
        ]

    def recent_events(
        self, service: str, environment: str | None = None, limit: int = 20
    ) -> list[LogEvent]:
        sql = (
            "SELECT id, ts, service, environment, severity, message, source, "
            "trace_id, request_id, attributes_json FROM log_events "
            "WHERE service = ?"
        )
        params: list[object] = [service]
        if environment is not None:
            sql += " AND environment = ?"
            params.append(environment)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            LogEvent(
                id=row["id"],
                ts=_from_iso(row["ts"]),
                service=row["service"],
                environment=row["environment"],
                severity=Severity(row["severity"]),
                message=row["message"],
                source=row["source"],
                trace_id=row["trace_id"],
                request_id=row["request_id"],
                attributes=json.loads(row["attributes_json"]),
            )
            for row in rows
        ]

    def prune_events(self, older_than: datetime) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM log_events WHERE ts < ?", (_iso(older_than),))
            return cursor.rowcount

    def prune_deliveries(self, older_than: datetime) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM deliveries WHERE state IN (?, ?) AND updated_at < ?",
                (
                    DeliveryState.COMPLETED.value,
                    DeliveryState.FAILED.value,
                    _iso(older_than),
                ),
            )
            return cursor.rowcount

    # --- secrets, deploy events, issue binding, backup --------------------------

    def get_secret(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM secrets WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_secret(self, key: str, value: str) -> None:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO secrets (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, now),
            )

    def delete_secret(self, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM secrets WHERE key = ?", (key,))

    def get_fingerprint_by_issue(self, issue_id: str) -> Fingerprint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM fingerprints WHERE ticket_issue_id = ? "
                "OR proposal_issue_id = ? ORDER BY last_seen DESC LIMIT 1",
                (issue_id, issue_id),
            ).fetchone()
        return None if row is None else _row_to_fingerprint(row)

    def get_fingerprint_by_branch(self, branch: str) -> Fingerprint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM fingerprints WHERE proposal_branch = ? "
                "ORDER BY last_seen DESC LIMIT 1",
                (branch,),
            ).fetchone()
        return None if row is None else _row_to_fingerprint(row)

    def save_deploy(self, event: DeployEvent) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO deployments (service, environment, sha, deployed_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    event.service,
                    event.environment,
                    event.sha,
                    _iso(event.deployed_at),
                ),
            )

    def list_recent_deploys(self, service: str, limit: int = 10) -> list[DeployEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM deployments WHERE service = ? ORDER BY deployed_at DESC LIMIT ?",
                (service, limit),
            ).fetchall()
        return [
            DeployEvent(
                service=row["service"],
                environment=row["environment"],
                sha=row["sha"],
                deployed_at=_from_iso(row["deployed_at"]),
            )
            for row in rows
        ]

    def backup(self, dest: Path) -> None:
        """Consistent online backup via SQLite's backup API (OPS-6)."""
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._conn.backup(target)
            finally:
                target.close()


def _row_to_fingerprint(row: sqlite3.Row) -> Fingerprint:
    columns = set(row.keys())
    return Fingerprint(
        fp_hash=row["fp_hash"],
        service=row["service"],
        environment=row["environment"],
        template=row["template"],
        frames=tuple(TraceFrame(**f) for f in json.loads(row["frames_json"])),
        severity=Severity(row["severity"]),
        first_seen=_from_iso(row["first_seen"]),
        last_seen=_from_iso(row["last_seen"]),
        occurrences=row["occurrences"],
        state=WorkState(row["state"]),
        ticket_issue_id=row["ticket_issue_id"],
        ticket_url=row["ticket_url"],
        commented_at_occurrences=int(row["commented_at_occurrences"])
        if "commented_at_occurrences" in columns
        else 0,
        last_comment_at=(
            _from_iso(row["last_comment_at"])
            if "last_comment_at" in columns and row["last_comment_at"]
            else None
        ),
        verification_started_at=(
            _from_iso(row["verification_started_at"])
            if "verification_started_at" in columns and row["verification_started_at"]
            else None
        ),
        verification_start_occurrences=int(row["verification_start_occurrences"])
        if "verification_start_occurrences" in columns
        else 0,
        chronic=bool(row["chronic"])
        if "chronic" in columns and row["chronic"] is not None
        else False,
        proposal_issue_id=row["proposal_issue_id"] if "proposal_issue_id" in columns else None,
        proposal_url=row["proposal_url"] if "proposal_url" in columns else None,
        proposal_branch=row["proposal_branch"] if "proposal_branch" in columns else None,
    )


def _fingerprint_params(
    fp: Fingerprint,
) -> tuple[object, ...]:  # keeps INSERT/UPDATE columns in sync
    return (
        fp.fp_hash,
        fp.service,
        fp.environment,
        fp.template,
        json.dumps([frame.model_dump() for frame in fp.frames]),
        fp.severity.value,
        _iso(fp.first_seen),
        _iso(fp.last_seen),
        fp.occurrences,
        fp.state.value,
        fp.ticket_issue_id,
        fp.ticket_url,
        fp.commented_at_occurrences,
        _iso(fp.last_comment_at) if fp.last_comment_at else None,
        _iso(fp.verification_started_at) if fp.verification_started_at else None,
        fp.verification_start_occurrences,
        int(fp.chronic),
        fp.proposal_issue_id,
        fp.proposal_url,
        fp.proposal_branch,
    )
