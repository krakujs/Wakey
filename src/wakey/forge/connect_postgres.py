# SPDX-License-Identifier: Apache-2.0
"""Postgres dialect boundary for the broker's small shared SQL schema.

The broker uses the same queries and security decisions on both backends. A
transaction-scoped advisory lock serializes bounded writes across Cloud Run
replicas (including deploy overlap), not merely threads in one process. This is
deliberately a low-throughput control plane; shard/refine locks before large scale.
"""

from __future__ import annotations

import sys
from types import TracebackType
from typing import Any

import psycopg
from psycopg.rows import dict_row


class ConnectPostgres:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.connection = self._connect()
        self.transaction: Any = None

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            self.dsn,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=10,
            options="-c statement_timeout=10000 -c lock_timeout=5000",
        )

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        if self.connection.closed:
            self.connection = self._connect()
        # Only trusted internal SQL uses this method; values remain bound parameters.
        return self.connection.execute(sql.replace("?", "%s"), parameters)

    def executescript(self, schema: str) -> None:
        schema = schema.replace("seq INTEGER PRIMARY KEY", "seq BIGSERIAL PRIMARY KEY")
        schema = schema.replace(" REAL ", " DOUBLE PRECISION ")
        with self:
            for statement in schema.split(";"):
                if statement.strip():
                    self.execute(statement)

    def __enter__(self) -> ConnectPostgres:
        if self.connection.closed:
            self.connection = self._connect()
        self.transaction = self.connection.transaction()
        self.transaction.__enter__()
        try:
            self.connection.execute("SELECT pg_advisory_xact_lock(84778480)")
        except Exception:
            self.transaction.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Any:
        return self.transaction.__exit__(kind, value, traceback)

    def close(self) -> None:
        self.connection.close()
