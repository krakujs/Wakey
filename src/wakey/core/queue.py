# SPDX-License-Identifier: Apache-2.0
"""Key-partitioned bounded queue + worker pool (E2-T5, WF-02 §7).

Partitioning by key gives the ordering guarantee the spec requires: all
events for one fingerprint land on the same partition and are processed
FIFO, while partitions process concurrently. Backpressure is explicit:
``put_nowait`` raises :class:`QueueFull` when a partition is full — the
ingest layer translates that into 429/dead-letter, never silent loss.
"""

from __future__ import annotations

import asyncio
import logging
import zlib
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class QueueFull(Exception):
    """A partition is at capacity — caller must apply backpressure."""


class KeyPartitionQueue:
    """Bounded, key-partitioned async queue with a worker per partition."""

    def __init__(self, partitions: int = 4, maxsize: int = 1000) -> None:
        if partitions < 1 or maxsize < 1:
            raise ValueError("partitions and maxsize must be >= 1")
        self._queues: list[asyncio.Queue[Any]] = [
            asyncio.Queue(maxsize=maxsize) for _ in range(partitions)
        ]
        self._tasks: list[asyncio.Task[None]] = []

    def _partition_index(self, key: str) -> int:
        return zlib.crc32(key.encode()) % len(self._queues)

    def put_nowait(self, item: Any, key: str) -> None:
        """Enqueue onto the item's key partition; raises QueueFull when full."""
        try:
            self._queues[self._partition_index(key)].put_nowait(item)
        except asyncio.QueueFull as exc:
            raise QueueFull(str(key)) from exc

    def pending(self) -> int:
        """Items still waiting across all partitions (metrics/health)."""
        return sum(queue.qsize() for queue in self._queues)

    def start(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        """Start one worker per partition (idempotent: no-op if running)."""
        if self._tasks:
            return

        async def worker(queue: asyncio.Queue[Any]) -> None:
            while True:
                item = await queue.get()
                try:
                    await handler(item)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # handler owns its error semantics; the worker must survive
                    logging.getLogger(__name__).exception("queue handler failed")
                finally:
                    queue.task_done()

        self._tasks = [asyncio.create_task(worker(queue)) for queue in self._queues]

    async def stop(self, drain: bool = True) -> None:
        """Stop workers; ``drain`` finishes everything queued *and in flight*."""
        if drain:
            # join() counts in-flight items too (qsize alone races with workers)
            await asyncio.gather(*(queue.join() for queue in self._queues))
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
