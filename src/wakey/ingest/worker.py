# SPDX-License-Identifier: Apache-2.0
"""Durable delivery worker (WF-02 §7, R-02): pending → processed, exactly once.

The ingest route only *persists* a delivery (state ``pending``) and
answers 202 — no parsing, forge calls, or model calls on the request
path. This worker claims pending deliveries, parses, sanitizes every
field (R-04), persists events, and dispatches the pipeline through a
per-event dispatch journal: a crash anywhere leaves the delivery
recoverable (startup resets stale ``processing`` rows; stored-but-
undispatched events are re-dispatched, and the fingerprint state
machine absorbs replays so retries never double-ticket). Bounded
retries dead-letter at ``max_attempts``.
"""

from __future__ import annotations

import asyncio
import logging

from wakey.core.metrics import MetricsRegistry
from wakey.core.models import Delivery
from wakey.core.storage import Storage
from wakey.ingest.normalizers import EventContext, parse_events
from wakey.ingest.pipeline import IngestPipeline
from wakey.security.redaction import RedactionEngine, sanitize_log_event

logger = logging.getLogger(__name__)


class DeliveryWorker:
    """Processes one pending delivery per :meth:`process_next` call."""

    def __init__(
        self,
        storage: Storage,
        pipeline: IngestPipeline,
        redactor: RedactionEngine,
        metrics: MetricsRegistry | None = None,
        max_attempts: int = 5,
    ) -> None:
        self._storage = storage
        self._pipeline = pipeline
        self._redactor = redactor
        self._metrics = metrics if metrics is not None else MetricsRegistry()
        self._max_attempts = max_attempts

    def process_next(self) -> bool:
        """Claim and process one pending delivery; True if one was handled."""
        delivery = self._storage.claim_next_delivery()
        if delivery is None:
            return False
        try:
            self._process(delivery)
        except Exception:
            state = self._storage.fail_delivery(
                delivery.service, delivery.delivery_id, self._max_attempts
            )
            self._metrics.inc("wakey_delivery_failures_total", "delivery processing failures")
            logger.exception(
                "delivery %s/%s failed (attempt %s -> %s)",
                delivery.service,
                delivery.delivery_id,
                delivery.attempts + 1,
                state.value,
            )
            return True
        self._storage.complete_delivery(delivery.service, delivery.delivery_id)
        return True

    def _process(self, delivery: Delivery) -> None:
        service = self._storage.get_service(delivery.service)
        environment = service.environment if service is not None else "prod"
        parsed = parse_events(
            delivery.payload,
            EventContext(
                service=delivery.service,
                environment=environment,
                source="ingest",
                delivery_id=delivery.delivery_id,
            ),
        )
        for _line, reason in parsed.dead_letters:
            self._metrics.inc(
                "wakey_dead_letters_total", "dead-lettered lines", {"reason": reason.split(":")[0]}
            )
            logger.warning(
                "delivery %s/%s dead letter: %s", delivery.service, delivery.delivery_id, reason
            )
        for event in parsed.events:
            clean = sanitize_log_event(event, self._redactor)
            fresh = self._storage.save_log_event(clean)
            if fresh or not self._storage.is_event_dispatched(clean.id):
                self._pipeline.handle_event(clean)
                self._storage.mark_event_dispatched(clean.id)
            # Dispatch journal (R-02): stored-but-undispatched events are
            # re-dispatched on retry; the fingerprint state machine absorbs
            # the replay so a retry can never double-ticket.


class DeliveryWorkerPool:
    """Bounded async consumers over the durable delivery table (E2-T5)."""

    def __init__(self, worker: DeliveryWorker, count: int = 2, poll_seconds: float = 0.25) -> None:
        if count < 1:
            raise ValueError("worker count must be >= 1")
        self._worker = worker
        self._count = count
        self._poll_seconds = poll_seconds
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._tasks:
            return

        async def loop() -> None:
            while True:
                processed = await asyncio.to_thread(self._worker.process_next)
                if not processed:
                    await asyncio.sleep(self._poll_seconds)

        self._tasks = [asyncio.ensure_future(loop()) for _ in range(self._count)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
