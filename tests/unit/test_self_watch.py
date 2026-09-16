# SPDX-License-Identifier: Apache-2.0
"""Self-watch tests (OPS-4, E11-T4): wakey errors flow through its own pipeline."""

from __future__ import annotations

import logging

from wakey.notify.self_watch import SelfWatchHandler


def test_error_records_flow_into_pipeline(tmp_path) -> None:
    delivered: list[object] = []
    handler = SelfWatchHandler(delivered.append)
    logger = logging.getLogger("wakey.test.subject")
    logger.addHandler(handler)
    try:
        logger.error("payments ledger write failed on shard 3")
    finally:
        logger.removeHandler(handler)
    assert len(delivered) == 1
    event = delivered[0]
    assert event.service == "wakey-self"  # type: ignore[attr-defined]
    assert "ledger write failed" in event.message  # type: ignore[attr-defined]


def test_reentrant_error_cannot_recurse(tmp_path) -> None:
    """An error raised while handling must not recurse or escape emit()."""
    calls: list[int] = []

    def exploding_deliver(event: object) -> None:
        calls.append(1)
        raise RuntimeError("boom while handling")

    handler = SelfWatchHandler(exploding_deliver)
    logger = logging.getLogger("wakey.test.recursive")
    logger.addHandler(handler)
    try:
        logger.error("first error")  # must not raise out of emit
    finally:
        logger.removeHandler(handler)
    assert len(calls) == 1, "the handling failure itself must not be re-delivered"


def test_below_error_severity_ignored() -> None:
    delivered: list[object] = []
    handler = SelfWatchHandler(delivered.append)
    logger = logging.getLogger("wakey.test.quiet")
    logger.addHandler(handler)
    try:
        logger.warning("just a warning")
    finally:
        logger.removeHandler(handler)
    assert delivered == []
