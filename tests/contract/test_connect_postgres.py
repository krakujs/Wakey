# SPDX-License-Identifier: Apache-2.0
"""Real PostgreSQL broker durability and cross-replica serialization checks."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from wakey.forge.connect_store import ConnectDenied, ConnectStore
from wakey.security.secretbox import SecretBox


@pytest.fixture
def stores():
    dsn = os.environ.get("WAKEYPG_TEST_DSN", "postgresql://postgres:wakey@127.0.0.1:55432/wakey")
    try:
        admin = psycopg.connect(dsn, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("WAKEYPG_REQUIRE_TESTS") == "1":
            raise
        pytest.skip("Postgres not reachable")
    name = "wakey_connect_test_" + uuid.uuid4().hex[:12]
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    connections = []
    try:
        for _ in range(2):
            connections.append(
                ConnectStore(
                    Path("unused"),
                    SecretBox("ab" * 32),
                    database_url=make_conninfo(dsn, dbname=name),
                )
            )
        yield connections
    finally:
        for store in connections:
            store.close()
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


def connect(store):
    credential = store.create({"device_code": "synthetic-device", "interval": 5}, 900)
    record = store.read(credential)
    assert 895 < record["expires"] - time.time() <= 900
    assert not store.claim_poll(record)
    store.write(record, "selecting", {"owner": 7, "repositories": []})
    record = store.read(credential)
    store.write(record, "connected", {"owner": 7, "repository": {"id": 11}})
    return credential


def test_postgres_restart_relay_and_revocation(stores):
    first, second = stores
    credential = connect(first)
    record = second.read(credential)
    assert record["state"] == "connected"
    raw = first.db.execute("SELECT data FROM connections WHERE id = ?", (record["id"],)).fetchone()
    assert "repository" not in raw["data"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda s: s.enqueue(record["id"], "delivery-1", {"kind": "ping"}), stores))
    assert len(second.events(credential)) == 1
    other = connect(second)
    assert first.events(other) == []
    second.acknowledge(other, "delivery-1")
    assert len(first.events(credential)) == 1
    second.acknowledge(credential, "delivery-1")
    first.enqueue(record["id"], "delivery-1", {"kind": "ping"})
    assert first.events(credential) == []
    first.db.close()
    assert first.read(credential)["state"] == "connected"
    second.revoke(credential)
    with pytest.raises(ConnectDenied):
        first.read(credential)
    with pytest.raises(ConnectDenied, match="changed"):
        first.write(record, "connected", {})


def test_postgres_global_admission_cap_across_replicas(stores):
    def admit(index):
        try:
            stores[index % 2].admit_pairing()
            return True
        except ConnectDenied:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(admit, range(61)))
    assert sum(results) == 60


def test_postgres_transaction_rolls_back(stores):
    first, second = stores
    with pytest.raises(ValueError), first.lock, first.db:
        first.db.execute("INSERT INTO admission VALUES (1, ?, 100)", (time.time(),))
        raise ValueError("synthetic rollback")
    assert second.db.execute("SELECT count FROM admission").fetchone() is None
