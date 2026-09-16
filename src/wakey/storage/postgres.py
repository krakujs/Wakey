# SPDX-License-Identifier: Apache-2.0
"""Postgres storage backend (OPS-5): durable state for production deploys.

Implements the full :class:`Storage` contract on psycopg3. Design mirrors
the SQLite backend so behavior is identical across backends:

- Timestamps are stored as ISO-8601 UTC text (lexicographic comparisons
  are therefore order-correct, matching the SQLite implementation).
- All access is serialized through one connection guarded by a lock —
  the same threading model; Cloud Run delivery workers and the FastAPI
  threadpool share the instance.
- ``claim_next_delivery`` uses ``FOR UPDATE SKIP LOCKED`` so multiple
  workers can safely compete for pending deliveries.
- Event dispatch uses the same journaled ``dispatched`` flag; the
  fingerprint upsert preserves DB-truth lifecycle columns on conflict.

Requires the ``postgres`` extra: ``pip install "wakey[postgres]"``.
Point ``WAKEY_DATABASE_URL`` at a ``postgresql://`` DSN (Cloud SQL,
RDS, or any Postgres 14+).
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

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
from wakey.core.storage import Storage
from wakey.security.secretbox import SecretBox

SCHEMA_VERSION = 1  # Postgres dialect version (independent of SQLite's)

_QUIET_STATES = (
    WorkState.CLOSED_AUTO,
    WorkState.CLOSED_HUMAN,
    WorkState.DROPPED,
    WorkState.VERIFIED_CLOSED,
    WorkState.ARCHIVED,
)

_FP_COLUMNS = (
    "fp_hash, service, environment, template, frames_json, severity, first_seen, "
    "last_seen, occurrences, state, ticket_issue_id, ticket_url, "
    "commented_at_occurrences, last_comment_at, verification_started_at, "
    "verification_start_occurrences, chronic, proposal_issue_id, proposal_url, "
    "proposal_branch"
)
_FP_PLACEHOLDERS = ", ".join(["%s"] * 20)

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
    name TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '.',
    forge TEXT NOT NULL DEFAULT 'github',
    environment TEXT NOT NULL DEFAULT 'prod',
    ingest_key_hash TEXT NOT NULL,
    webhook_secret TEXT,
    config_json TEXT NOT NULL DEFAULT '{}'
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
    attributes_json TEXT NOT NULL DEFAULT '{}',
    dispatched INTEGER NOT NULL DEFAULT 0
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
    ticket_url TEXT,
    commented_at_occurrences INTEGER NOT NULL DEFAULT 0,
    last_comment_at TEXT,
    verification_started_at TEXT,
    verification_start_occurrences INTEGER NOT NULL DEFAULT 0,
    chronic INTEGER NOT NULL DEFAULT 0,
    proposal_issue_id TEXT,
    proposal_url TEXT,
    proposal_branch TEXT
);
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
CREATE INDEX IF NOT EXISTS idx_deliveries_state ON deliveries (state, created_at);
CREATE TABLE IF NOT EXISTS secrets (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    id BIGSERIAL PRIMARY KEY,
    service TEXT NOT NULL,
    environment TEXT NOT NULL,
    sha TEXT NOT NULL,
    deployed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deploys_service_ts ON deployments (service, deployed_at);
CREATE TABLE IF NOT EXISTS audit (
    seq BIGSERIAL PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    prev_hash TEXT,
    entry_hash TEXT
);
"""


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def __now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class PostgresStorage(Storage):
    """Full Storage contract on Postgres (NFR-2 durability, OPS-5)."""

    def __init__(self, dsn: str, secret_box: SecretBox | None = None) -> None:
        self._lock = threading.Lock()
        self._dsn = dsn
        self._secret_box = secret_box
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self._conn.autocommit = True
        self._migrate()

    # --- schema -----------------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock, self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_PG_SCHEMA)
            cur.execute(
                "INSERT INTO meta (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    # --- core ---------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def check_ready(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1")
            return True
        except psycopg.Error:
            return False

    # --- deliveries ---------------------------------------------------------------

    def save_delivery(self, delivery: Delivery) -> bool:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                "INSERT INTO deliveries (service, delivery_id, state, payload, attempts, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (service, delivery_id) DO NOTHING",
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
            return cur.rowcount == 1

    def claim_next_delivery(self) -> Delivery | None:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                "UPDATE deliveries SET state = %s, updated_at = %s "
                "WHERE (service, delivery_id) IN ("
                "  SELECT service, delivery_id FROM deliveries"
                "  WHERE state = %s ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                ") RETURNING service, delivery_id, payload, attempts, created_at, updated_at",
                (DeliveryState.PROCESSING.value, now, DeliveryState.PENDING.value),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return Delivery(
                service=row["service"],
                delivery_id=row["delivery_id"],
                state=DeliveryState.PROCESSING,
                payload=row["payload"],
                attempts=row["attempts"],
                created_at=_from_iso(row["created_at"]),
                updated_at=_from_iso(row["updated_at"]),
            )

    def complete_delivery(self, service: str, delivery_id: str) -> None:
        with self._lock, self._conn.transaction():
            self._conn.execute(
                "UPDATE deliveries SET state = %s, updated_at = %s "
                "WHERE service = %s AND delivery_id = %s",
                (DeliveryState.COMPLETED.value, _now_iso(), service, delivery_id),
            )

    def fail_delivery(self, service: str, delivery_id: str, max_attempts: int) -> DeliveryState:
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                "SELECT attempts FROM deliveries WHERE service = %s AND delivery_id = %s",
                (service, delivery_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"delivery not found: {service}/{delivery_id}")
            attempts = int(row["attempts"]) + 1
            state = DeliveryState.FAILED if attempts >= max_attempts else DeliveryState.PENDING
            self._conn.execute(
                "UPDATE deliveries SET attempts = %s, state = %s, updated_at = %s "
                "WHERE service = %s AND delivery_id = %s",
                (attempts, state.value, _now_iso(), service, delivery_id),
            )
            return state

    def recover_stale_deliveries(self) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                "UPDATE deliveries SET state = %s, updated_at = %s WHERE state = %s",
                (DeliveryState.PENDING.value, _now_iso(), DeliveryState.PROCESSING.value),
            )
            return cur.rowcount

    def pending_delivery_count(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM deliveries WHERE state IN (%s, %s)",
                (DeliveryState.PENDING.value, DeliveryState.PROCESSING.value),
            )
            row = cur.fetchone()
            return int(row["n"]) if row is not None else 0

    # --- services -------------------------------------------------------------------

    def save_service(self, service: Service) -> None:
        webhook_secret = service.webhook_secret
        if self._secret_box is not None and webhook_secret is not None:
            webhook_secret = self._secret_box.encrypt(webhook_secret)
        with self._lock, self._conn.transaction():
            self._conn.execute(
                "INSERT INTO services (name, repo, path, forge, environment, "
                "ingest_key_hash, webhook_secret, config_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET repo = EXCLUDED.repo, "
                "path = EXCLUDED.path, forge = EXCLUDED.forge, "
                "environment = EXCLUDED.environment, "
                "ingest_key_hash = EXCLUDED.ingest_key_hash, "
                "webhook_secret = EXCLUDED.webhook_secret, "
                "config_json = EXCLUDED.config_json",
                (
                    service.name,
                    service.repo,
                    service.path,
                    service.forge,
                    service.environment,
                    service.ingest_key_hash,
                    webhook_secret,
                    service.config_json,
                ),
            )

    def get_service(self, name: str) -> Service | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM services WHERE name = %s", (name,)).fetchone()
        return None if row is None else self._service_from_row(row)

    def get_service_by_ingest_key_hash(self, key_hash: str) -> Service | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM services WHERE ingest_key_hash = %s", (key_hash,)
            ).fetchone()
        return None if row is None else self._service_from_row(row)

    def has_services(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 AS one FROM services LIMIT 1").fetchone()
        return row is not None

    def list_services(self) -> list[Service]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM services ORDER BY name").fetchall()
        return [self._service_from_row(row) for row in rows]

    def _service_from_row(self, row: dict[str, Any]) -> Service:
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
            config_json=row.get("config_json", "{}"),
        )

    def record_occurrence(self, incoming: Fingerprint, delta: int = 1) -> Fingerprint:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                f"INSERT INTO fingerprints ({_FP_COLUMNS}) "
                f"VALUES ({_FP_PLACEHOLDERS}) "
                "ON CONFLICT (fp_hash) DO UPDATE SET "
                "template = EXCLUDED.template, frames_json = EXCLUDED.frames_json, "
                "severity = EXCLUDED.severity, last_seen = EXCLUDED.last_seen, "
                "occurrences = fingerprints.occurrences + %s "
                "RETURNING *",
                (
                    incoming.fp_hash,
                    incoming.service,
                    incoming.environment,
                    incoming.template,
                    json.dumps([f.model_dump() for f in incoming.frames]),
                    incoming.severity.value,
                    _iso(incoming.first_seen),
                    _iso(incoming.last_seen),
                    incoming.occurrences,
                    incoming.state.value,
                    incoming.ticket_issue_id,
                    incoming.ticket_url,
                    incoming.commented_at_occurrences,
                    _iso(incoming.last_comment_at) if incoming.last_comment_at else None,
                    _iso(incoming.verification_started_at)
                    if incoming.verification_started_at
                    else None,
                    incoming.verification_start_occurrences,
                    int(incoming.chronic),
                    incoming.proposal_issue_id,
                    incoming.proposal_url,
                    incoming.proposal_branch,
                    delta,
                ),
            )
            row = cur.fetchone()
            if row is None:  # pragma: no cover — upsert always returns a row
                raise RuntimeError("fingerprint upsert returned no row")
        return self._fp_from_row(row)

    def get_fingerprint(self, fp_hash: str) -> Fingerprint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM fingerprints WHERE fp_hash = %s", (fp_hash,)
            ).fetchone()
        return None if row is None else self._fp_from_row(row)

    def get_fingerprint_by_issue(self, issue_id: str) -> Fingerprint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM fingerprints WHERE ticket_issue_id = %s "
                "OR proposal_issue_id = %s ORDER BY last_seen DESC LIMIT 1",
                (issue_id, issue_id),
            ).fetchone()
        return None if row is None else self._fp_from_row(row)

    def get_fingerprint_by_branch(self, branch: str) -> Fingerprint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM fingerprints WHERE proposal_branch = %s "
                "ORDER BY last_seen DESC LIMIT 1",
                (branch,),
            ).fetchone()
        return None if row is None else self._fp_from_row(row)

    def save_fingerprint(self, fingerprint: Fingerprint) -> None:
        with self._lock, self._conn.transaction():
            self._conn.execute(
                f"INSERT INTO fingerprints ({_FP_COLUMNS}) "
                f"VALUES ({_FP_PLACEHOLDERS}) "
                "ON CONFLICT (fp_hash) DO UPDATE SET "
                "template = EXCLUDED.template, frames_json = EXCLUDED.frames_json, "
                "severity = EXCLUDED.severity, last_seen = EXCLUDED.last_seen, "
                "occurrences = EXCLUDED.occurrences, state = EXCLUDED.state, "
                "ticket_issue_id = EXCLUDED.ticket_issue_id, ticket_url = EXCLUDED.ticket_url, "
                "commented_at_occurrences = EXCLUDED.commented_at_occurrences, "
                "last_comment_at = EXCLUDED.last_comment_at, "
                "verification_started_at = EXCLUDED.verification_started_at, "
                "verification_start_occurrences = EXCLUDED.verification_start_occurrences, "
                "chronic = EXCLUDED.chronic, proposal_issue_id = EXCLUDED.proposal_issue_id, "
                "proposal_url = EXCLUDED.proposal_url, proposal_branch = EXCLUDED.proposal_branch",
                _fingerprint_params(fingerprint),
            )

    def list_active_fingerprints(self, service: str | None = None) -> list[Fingerprint]:
        quiet = ",".join("?" for _ in _QUIET_STATES)
        with self._lock:
            if service is None:
                rows = self._conn.execute(
                    f"SELECT * FROM fingerprints WHERE state NOT IN ({quiet}) "
                    "ORDER BY last_seen DESC",
                    tuple(s.value for s in _QUIET_STATES),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT * FROM fingerprints WHERE state NOT IN ({quiet}) "
                    "AND service = %s ORDER BY last_seen DESC",
                    tuple(s.value for s in _QUIET_STATES) + (service,),
                ).fetchall()
        return [self._fp_from_row(row) for row in rows]

    @staticmethod
    def _fp_from_row(row: dict[str, Any]) -> Fingerprint:
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
            commented_at_occurrences=row["commented_at_occurrences"],
            last_comment_at=(_from_iso(row["last_comment_at"]) if row["last_comment_at"] else None),
            verification_started_at=(
                _from_iso(row["verification_started_at"])
                if row["verification_started_at"]
                else None
            ),
            verification_start_occurrences=row["verification_start_occurrences"],
            chronic=bool(row["chronic"]),
            proposal_issue_id=row["proposal_issue_id"],
            proposal_url=row["proposal_url"],
            proposal_branch=row["proposal_branch"],
        )

    # --- events & audit -----------------------------------------------------------

    def save_log_event(self, event: LogEvent) -> bool:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                "INSERT INTO log_events (id, ts, service, environment, severity, "
                "message, source, trace_id, request_id, attributes_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
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
            return cur.rowcount == 1

    def is_event_dispatched(self, event_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT dispatched FROM log_events WHERE id = %s", (event_id,)
            ).fetchone()
        return bool(row and row["dispatched"])

    def mark_event_dispatched(self, event_id: str) -> None:
        with self._lock, self._conn.transaction():
            self._conn.execute("UPDATE log_events SET dispatched = 1 WHERE id = %s", (event_id,))

    def record_audit(self, event: AuditEvent) -> None:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn.transaction():
            prev = self._conn.execute(
                "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev["entry_hash"] if prev else None
            entry_hash = _audit_entry_hash(
                prev_hash,
                (now, event.actor, event.action, event.subject, json.dumps(event.details)),
            )
            self._conn.execute(
                "INSERT INTO audit (ts, actor, action, subject, details_json, "
                "prev_hash, entry_hash) VALUES (%s, %s, %s, %s, %s, %s, %s)",
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
        prev = None
        for index, row in enumerate(rows):
            expected = _audit_entry_hash(
                prev, (row["ts"], row["actor"], row["action"], row["subject"], row["details_json"])
            )
            if row["entry_hash"] != expected or row["prev_hash"] != prev:
                return False, index
            prev = row["entry_hash"]
        return True, len(rows)

    # --- secrets ---------------------------------------------------------------------

    def get_secret(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM secrets WHERE key = %s", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_secret(self, key: str, value: str) -> None:
        now = _iso(datetime.now(UTC))
        with self._lock, self._conn.transaction():
            self._conn.execute(
                "INSERT INTO secrets (key, value, updated_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "updated_at = EXCLUDED.updated_at",
                (key, value, now),
            )

    def delete_secret(self, key: str) -> None:
        with self._lock, self._conn.transaction():
            self._conn.execute("DELETE FROM secrets WHERE key = %s", (key,))

    # --- deploys ------------------------------------------------------------------------

    def save_deploy(self, event: DeployEvent) -> None:
        with self._lock, self._conn.transaction():
            self._conn.execute(
                "INSERT INTO deployments (service, environment, sha, deployed_at) "
                "VALUES (%s, %s, %s, %s)",
                (event.service, event.environment, event.sha, _iso(event.deployed_at)),
            )

    def list_recent_deploys(self, service: str, limit: int = 10) -> list[DeployEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM deployments WHERE service = %s ORDER BY deployed_at DESC LIMIT %s",
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

    # --- audit listing & event queries -------------------------------------------

    def list_audit(self, subject: str | None = None, limit: int = 50) -> list[AuditEvent]:
        with self._lock:
            if subject is not None:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE subject = %s ORDER BY seq DESC LIMIT %s",
                    (subject, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit ORDER BY seq DESC LIMIT %s", (limit,)
                ).fetchall()
        events: list[AuditEvent] = []
        for row in rows:
            events.append(
                AuditEvent(
                    ts=_from_iso(row["ts"]),
                    actor=row["actor"],
                    action=row["action"],
                    subject=row["subject"],
                    details=json.loads(row["details_json"]),
                )
            )
        return events

    def recent_events(
        self, service: str, environment: str | None = None, limit: int = 20
    ) -> list[LogEvent]:
        with self._lock:
            if environment is not None:
                rows = self._conn.execute(
                    "SELECT * FROM log_events WHERE service = %s AND environment = %s "
                    "ORDER BY ts DESC LIMIT %s",
                    (service, environment, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM log_events WHERE service = %s ORDER BY ts DESC LIMIT %s",
                    (service, limit),
                ).fetchall()
        return [
            LogEvent(
                id=row["id"],
                ts=_from_iso(row["ts"]),
                service=row["service"],
                environment=row["environment"],
                severity=Severity(row["severity"]),
                message=row["message"],
                source=row["source"],
                frames=(),
                trace_id=row["trace_id"],
                request_id=row["request_id"],
                attributes=json.loads(row["attributes_json"]),
            )
            for row in rows
        ]

    def prune_events(self, older_than: datetime) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute("DELETE FROM log_events WHERE ts < %s", (_iso(older_than),))
            return cur.rowcount

    def prune_deliveries(self, older_than: datetime) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                "DELETE FROM deliveries WHERE state IN (%s, %s) AND updated_at < %s",
                (
                    DeliveryState.COMPLETED.value,
                    DeliveryState.FAILED.value,
                    _iso(older_than),
                ),
            )
            return cur.rowcount

    def backup(self, dest: Path) -> None:
        raise NotImplementedError(
            "Postgres backups use pg_dump — see docs/operations.md §Backup and restore"
        )


def _fingerprint_params(fp: Fingerprint) -> tuple[object, ...]:
    """Column order mirrors the fingerprints table (save_fingerprint)."""
    return (
        fp.fp_hash,
        fp.service,
        fp.environment,
        fp.template,
        json.dumps([f.model_dump() for f in fp.frames]),
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


def _audit_entry_hash(prev_hash: str | None, row_values: tuple[object, ...]) -> str:
    material = "|".join(str(v) for v in (prev_hash or "", *row_values))
    return hashlib.sha256(material.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
