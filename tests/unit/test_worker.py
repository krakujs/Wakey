# SPDX-License-Identifier: Apache-2.0
"""Delivery worker tests: bounded retries, dead-lettering, pool lifecycle (R-02)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Delivery
from wakey.core.storage import SQLiteStorage
from wakey.forge.port import ConsoleForge
from wakey.ingest.pipeline import IngestPipeline
from wakey.ingest.worker import DeliveryWorker, DeliveryWorkerPool
from wakey.security.redaction import RedactionEngine


def make_worker(storage: SQLiteStorage, max_attempts: int = 3) -> DeliveryWorker:
    return DeliveryWorker(
        storage,
        IngestPipeline(storage, ConsoleForge()),
        RedactionEngine(),
        metrics=MetricsRegistry(),
        max_attempts=max_attempts,
    )


def test_empty_queue_reports_idle(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    worker = make_worker(storage)
    assert worker.process_next() is False
    storage.close()


class ExplodingPipeline(IngestPipeline):
    """Always-failing dispatch: the poison-delivery generator."""

    def handle_event(self, event: object) -> object:  # noqa: ARG002
        raise RuntimeError("synthetic dispatch failure")


def test_poison_delivery_dead_letters_at_attempt_cap(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    storage.save_service(
        __import__("wakey.core.models", fromlist=["Service"]).Service(
            name="acme", repo="acme/x", ingest_key_hash="0123456789abcdef"
        )
    )
    storage.save_delivery(Delivery(service="acme", delivery_id="d-1", payload="ERROR: boom"))
    worker = DeliveryWorker(
        storage, ExplodingPipeline(storage, ConsoleForge()), RedactionEngine(), max_attempts=3
    )

    assert worker.process_next() is True  # attempt 1 -> pending
    assert worker.process_next() is True  # attempt 2 -> pending
    assert worker.process_next() is True  # attempt 3 -> failed (cap)

    assert storage.pending_delivery_count() == 0, "dead-lettered delivery leaves the queue"
    assert worker.process_next() is False

    state = storage._conn.execute(  # noqa: SLF001
        "SELECT state, attempts FROM deliveries WHERE delivery_id = 'd-1'"
    ).fetchone()
    assert state["state"] == "failed" and state["attempts"] == 3
    storage.close()


def test_poison_delivery_rolls_back_and_retries_payload(tmp_path: Path) -> None:
    """The failure mode here: parse/dispatch raises, delivery is retried, not lost."""
    storage = SQLiteStorage(tmp_path / "wakey.db")

    class ExplodingPipeline(IngestPipeline):
        def handle_event(self, event: object) -> object:  # noqa: ARG002
            raise RuntimeError("synthetic dispatch failure")

    storage.save_service(
        __import__("wakey.core.models", fromlist=["Service"]).Service(
            name="acme", repo="acme/x", ingest_key_hash="0123456789abcdef"
        )
    )
    storage.save_delivery(Delivery(service="acme", delivery_id="d-1", payload="ERROR: boom"))
    worker = DeliveryWorker(
        storage, ExplodingPipeline(storage, ConsoleForge()), RedactionEngine(), max_attempts=2
    )
    assert worker.process_next() is True
    assert storage.pending_delivery_count() == 1  # recoverable
    storage.close()


def test_pool_start_stop_is_idempotent(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "wakey.db")
    pool = DeliveryWorkerPool(make_worker(storage), count=2, poll_seconds=0.05)

    async def lifecycle() -> None:
        await pool.start()
        snapshot = list(pool._tasks)
        await pool.start()  # second start must not spawn duplicates
        assert pool._tasks == snapshot
        await pool.stop()
        assert pool._tasks == []

    asyncio.run(lifecycle())
    storage.close()
