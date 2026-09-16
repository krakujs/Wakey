# SPDX-License-Identifier: Apache-2.0
"""Storage: abstract interface + SQLite implementation (E2-T3).

The rest of Wakey depends on :class:`Storage`, never on SQLite directly —
the Postgres path (OPS-5) and the in-memory test fake implement the same
interface. Concurrency model: one connection guarded by a lock (server
endpoints run in FastAPI's threadpool); queue workers (E2-T5) serialize
through the same instance.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

from wakey.core.models import (
    AuditEvent,
    Fingerprint,
    LogEvent,
    Service,
    Severity,
    TraceFrame,
    WorkState,
)

SCHEMA_VERSION = 1

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

    @abstractmethod
    def record_delivery(self, delivery_id: str) -> bool:
        """True if this delivery id was seen for the first time (WF-02 idempotency)."""

    @abstractmethod
    def save_service(self, service: Service) -> None: ...

    @abstractmethod
    def get_service(self, name: str) -> Service | None: ...

    @abstractmethod
    def get_service_by_ingest_key_hash(self, key_hash: str) -> Service | None: ...

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
    def save_log_event(self, event: LogEvent) -> None: ...

    @abstractmethod
    def record_audit(self, event: AuditEvent) -> None: ...


class SQLiteStorage(Storage):
    """Single-file storage behind the Storage interface (NFR-2, OPS-5)."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            current = int(row[0]) if row else 0
            if current < 1:
                self._conn.executescript(_SCHEMA_V1)
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

    # --- delivery idempotency ------------------------------------------------

    def record_delivery(self, delivery_id: str) -> bool:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO delivery_ids (delivery_id, seen_at) VALUES (?, ?) "
                "ON CONFLICT(delivery_id) DO NOTHING",
                (delivery_id, now),
            )
            return cursor.rowcount == 1

    # --- services ------------------------------------------------------------

    def save_service(self, service: Service) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO services (name, repo, path, forge, environment, ingest_key_hash) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "repo=excluded.repo, path=excluded.path, forge=excluded.forge, "
                "environment=excluded.environment, ingest_key_hash=excluded.ingest_key_hash",
                (
                    service.name,
                    service.repo,
                    service.path,
                    service.forge,
                    service.environment,
                    service.ingest_key_hash,
                ),
            )

    def get_service(self, name: str) -> Service | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM services WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return Service(
            name=row["name"],
            repo=row["repo"],
            path=row["path"],
            forge=row["forge"],
            environment=row["environment"],
            ingest_key_hash=row["ingest_key_hash"],
        )

    def get_service_by_ingest_key_hash(self, key_hash: str) -> Service | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM services WHERE ingest_key_hash = ?", (key_hash,)
            ).fetchone()
        if row is None:
            return None
        return Service(
            name=row["name"],
            repo=row["repo"],
            path=row["path"],
            forge=row["forge"],
            environment=row["environment"],
            ingest_key_hash=row["ingest_key_hash"],
        )

    # --- fingerprints --------------------------------------------------------

    def record_occurrence(self, incoming: Fingerprint, delta: int = 1) -> Fingerprint:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT occurrences, first_seen, state, ticket_issue_id, ticket_url "
                "FROM fingerprints WHERE fp_hash = ?",
                (incoming.fp_hash,),
            ).fetchone()
            if row is None:
                stored = incoming.model_copy(update={"occurrences": incoming.occurrences + delta})
                self._conn.execute(
                    "INSERT INTO fingerprints (fp_hash, service, environment, template, "
                    "frames_json, severity, first_seen, last_seen, occurrences, state, "
                    "ticket_issue_id, ticket_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _fingerprint_params(stored),
                )
                return stored

            merged = incoming.model_copy(
                update={
                    "occurrences": int(row["occurrences"]) + delta,
                    "first_seen": _from_iso(row["first_seen"]),
                    # counters change; lifecycle state and ticket refs are DB truth
                    "state": WorkState(row["state"]),
                    "ticket_issue_id": row["ticket_issue_id"],
                    "ticket_url": row["ticket_url"],
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
        return Fingerprint(
            fp_hash=row["fp_hash"],
            service=row["service"],
            environment=row["environment"],
            template=row["template"],
            frames=tuple(TraceFrame(**frame) for frame in json.loads(row["frames_json"])),
            severity=Severity(row["severity"]),
            first_seen=_from_iso(row["first_seen"]),
            last_seen=_from_iso(row["last_seen"]),
            occurrences=row["occurrences"],
            state=WorkState(row["state"]),
            ticket_issue_id=row["ticket_issue_id"],
            ticket_url=row["ticket_url"],
        )

    def save_fingerprint(self, fingerprint: Fingerprint) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO fingerprints (fp_hash, service, environment, template, "
                "frames_json, severity, first_seen, last_seen, occurrences, state, "
                "ticket_issue_id, ticket_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(fp_hash) DO UPDATE SET template=excluded.template, "
                "frames_json=excluded.frames_json, severity=excluded.severity, "
                "last_seen=excluded.last_seen, occurrences=excluded.occurrences, "
                "state=excluded.state, ticket_issue_id=excluded.ticket_issue_id, "
                "ticket_url=excluded.ticket_url",
                _fingerprint_params(fingerprint),
            )

    # --- events & audit ------------------------------------------------------

    def save_log_event(self, event: LogEvent) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO log_events (id, ts, service, environment, severity, "
                "message, source, trace_id, request_id, attributes_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    def record_audit(self, event: AuditEvent) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit (ts, actor, action, subject, details_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    _iso(event.ts),
                    event.actor,
                    event.action,
                    event.subject,
                    json.dumps(event.details),
                ),
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
    )
