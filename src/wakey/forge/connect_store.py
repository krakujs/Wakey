# SPDX-License-Identifier: Apache-2.0
"""Single-instance Connect broker state, separate from customer incident storage.

All reversible state (device codes, repository metadata, webhook envelopes) is
encrypted. Deployment credentials are verification-only hashes. SQLite supplies
restart persistence and atomic claims; public HA hosting needs a shared backend.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wakey.security.secretbox import SecretBox, hash_token

if TYPE_CHECKING:
    from wakey.forge.connect_postgres import ConnectPostgres


class ConnectDenied(Exception):
    """Expired, unbound or unauthorized deployment request."""


class ConnectStore:
    def __init__(self, path: Path, box: SecretBox, *, database_url: str = "") -> None:
        self.box = box
        self.lock = threading.RLock()
        self.db: sqlite3.Connection | ConnectPostgres
        if database_url:
            from wakey.forge.connect_postgres import ConnectPostgres  # noqa: PLC0415

            self.db = ConnectPostgres(database_url)
        else:
            sqlite = sqlite3.connect(path, check_same_thread=False)
            sqlite.row_factory = sqlite3.Row
            self.db = sqlite
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY, credential TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL, expires REAL NOT NULL, next_poll REAL NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS relay (
                connection TEXT NOT NULL, delivery TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(connection, delivery)
            );
            CREATE TABLE IF NOT EXISTS admission (
                id INTEGER PRIMARY KEY CHECK(id = 1), started REAL NOT NULL, count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                seq INTEGER PRIMARY KEY, at REAL NOT NULL,
                connection TEXT NOT NULL, action TEXT NOT NULL
            );
        """)

    def admit_pairing(self) -> None:
        """Global admission cap before contacting GitHub; edge per-IP limits also required."""
        now = time.time()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO admission VALUES (1, ?, 1) ON CONFLICT(id) DO UPDATE SET "
                "count = CASE WHEN admission.started < ? THEN 1 ELSE admission.count + 1 END, "
                "started = CASE WHEN admission.started < ? THEN ? ELSE admission.started END",
                (now, now - 60, now - 60, now),
            )
            count = self.db.execute("SELECT count FROM admission WHERE id = 1").fetchone()["count"]
        if count > 60:
            raise ConnectDenied("pairing rate limit reached; retry in one minute")

    def audit(self, identity: str, action: str) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO actions(at, connection, action) VALUES (?, ?, ?)",
                (time.time(), identity, action),
            )

    def create(self, data: dict[str, Any], ttl: int) -> str:
        credential = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock, self.db:
            self.db.execute(
                "DELETE FROM connections WHERE state != 'connected' AND expires < ?", (now,)
            )
            count = self.db.execute(
                "SELECT COUNT(*) AS count FROM connections WHERE state != 'connected'"
            ).fetchone()["count"]
            if count >= 1000:
                raise ConnectDenied("pairing capacity reached; retry later")
            self.db.execute(
                "INSERT INTO connections VALUES (?, ?, 'pending', ?, ?, ?)",
                (
                    secrets.token_hex(16),
                    hash_token(credential),
                    now + ttl,
                    now + int(data["interval"]),
                    self.box.encrypt(json.dumps(data)),
                ),
            )
        return credential

    def read(self, credential: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM connections WHERE credential = ?", (hash_token(credential),)
            ).fetchone()
        if row is None or row["state"] == "revoked":
            raise ConnectDenied("connection not authorized")
        if row["state"] != "connected" and row["expires"] < time.time():
            raise ConnectDenied("pairing expired; start again")
        return {**dict(row), "data": json.loads(self.box.decrypt(row["data"]))}

    def write(self, connection: dict[str, Any], state: str, data: dict[str, Any]) -> None:
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE connections SET state = ?, data = ?, next_poll = ? "
                "WHERE id = ? AND state = ?",
                (
                    state,
                    self.box.encrypt(json.dumps(data)),
                    time.time() + int(data.get("interval", 5)),
                    connection["id"],
                    connection["state"],
                ),
            ).rowcount
        if not changed:
            raise ConnectDenied("connection changed; retry")
        self.audit(connection["id"], state)

    def claim_poll(self, connection: dict[str, Any]) -> bool:
        now = time.time()
        with self.lock, self.db:
            return bool(
                self.db.execute(
                    "UPDATE connections SET next_poll = ? WHERE id = ? "
                    "AND state = 'pending' AND next_poll <= ?",
                    (now + int(connection["data"]["interval"]), connection["id"], now),
                ).rowcount
            )

    def revoke(self, credential: str) -> None:
        record = self.read(credential)
        self.revoke_id(record["id"])

    def revoke_id(self, identity: str) -> None:
        with self.lock, self.db:
            self.db.execute(
                "UPDATE connections SET state = 'revoked', data = ? WHERE id = ?",
                (self.box.encrypt("{}"), identity),
            )
            self.db.execute("DELETE FROM relay WHERE connection = ?", (identity,))
        self.audit(identity, "revoked")

    def connected(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM connections WHERE state = 'connected'").fetchall()
        return [{**dict(row), "data": json.loads(self.box.decrypt(row["data"]))} for row in rows]

    def enqueue(self, identity: str, delivery: str, envelope: dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM relay WHERE created < ?", (time.time() - 86400,))
            existing = self.db.execute(
                "SELECT 1 FROM relay WHERE connection = ? AND delivery = ?", (identity, delivery)
            ).fetchone()
            if existing:
                return
            count = self.db.execute(
                "SELECT COUNT(*) AS count FROM relay WHERE connection = ? AND acknowledged = 0",
                (identity,),
            ).fetchone()["count"]
            if count >= 1000:
                raise ConnectDenied("relay queue full; retry delivery")
            self.db.execute(
                "INSERT INTO relay VALUES (?, ?, ?, ?, 0)",
                (identity, delivery, self.box.encrypt(json.dumps(envelope)), time.time()),
            )

    def events(self, credential: str) -> list[dict[str, Any]]:
        record = self.read(credential)
        if record["state"] != "connected":
            raise ConnectDenied("select a repository first")
        with self.lock:
            rows = self.db.execute(
                "SELECT delivery, payload FROM relay WHERE connection = ? AND acknowledged = 0 "
                "AND created >= ? ORDER BY created LIMIT 25",
                (record["id"], time.time() - 86400),
            ).fetchall()
        return [
            {"id": row["delivery"], **json.loads(self.box.decrypt(row["payload"]))} for row in rows
        ]

    def acknowledge(self, credential: str, delivery: str) -> None:
        record = self.read(credential)
        with self.lock, self.db:
            self.db.execute(
                "UPDATE relay SET acknowledged = 1 WHERE connection = ? AND delivery = ?",
                (record["id"], delivery),
            )

    def close(self) -> None:
        self.db.close()
