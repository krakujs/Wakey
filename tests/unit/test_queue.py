# SPDX-License-Identifier: Apache-2.0
"""Tests: per-key ordering, backpressure, graceful drain (E2-T5 AC)."""

from __future__ import annotations

import asyncio

import pytest

from wakey.core.queue import KeyPartitionQueue, QueueFull


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_per_key_order_preserved_under_concurrency() -> None:
    async def scenario() -> list[tuple[str, int]]:
        processed: list[tuple[str, int]] = []

        async def handler(item: tuple[str, int]) -> None:
            await asyncio.sleep(0)  # yield to force interleaving
            processed.append(item)

        queue = KeyPartitionQueue(partitions=3, maxsize=100)
        queue.start(handler)
        for key in ("fp-a", "fp-b", "fp-c"):
            for seq in range(10):
                queue.put_nowait((key, seq), key=key)
        await queue.stop(drain=True)
        return processed

    processed = run(scenario())
    assert len(processed) == 30
    for key in ("fp-a", "fp-b", "fp-c"):
        seqs = [seq for k, seq in processed if k == key]
        assert seqs == sorted(seqs), f"{key} out of order: {seqs}"


def test_burst_all_processed_and_backpressure_signal() -> None:
    async def scenario() -> tuple[int, object]:
        processed = 0

        async def handler(item: int) -> None:
            nonlocal processed
            processed += 1
            await asyncio.sleep(0)

        queue = KeyPartitionQueue(partitions=1, maxsize=2)
        queue.start(handler)
        first_full: object = None
        i = 0
        while i < 200:  # producer retries the same item on backpressure
            try:
                queue.put_nowait(i, key="k")
                i += 1
            except QueueFull as exc:
                first_full = first_full or exc
                await asyncio.sleep(0)  # let the worker drain one
        await queue.stop(drain=True)
        return processed, first_full

    processed, saw_full = run(scenario())
    assert processed == 200
    assert isinstance(saw_full, QueueFull)


def test_stop_without_drain_discards_pending() -> None:
    async def scenario() -> int:
        processed = 0

        async def handler(item: int) -> None:
            nonlocal processed
            processed += 1
            await asyncio.sleep(0.01)

        queue = KeyPartitionQueue(partitions=1, maxsize=100)
        queue.start(handler)
        for i in range(20):
            queue.put_nowait(i, key="k")
        await asyncio.sleep(0.03)  # a few processed, most still pending
        await queue.stop(drain=False)
        return processed

    processed = run(scenario())
    assert 0 < processed < 20


def test_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        KeyPartitionQueue(partitions=0)
    with pytest.raises(ValueError):
        KeyPartitionQueue(maxsize=0)
