# SPDX-License-Identifier: Apache-2.0
"""Self-watch (OPS-4, E11-T4): wakey watches its own errors.

A logging handler feeds ERROR+ records through the real pipeline so the
operator's own monitoring loop can ticket itself. A reentrancy sentinel
prevents the obvious failure loop: an error *while handling* an error
must never recurse (the record is dropped and logged once, quietly).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable

from wakey.core.models import LogEvent, Severity, utcnow

_LEVEL_MAP = {
    logging.ERROR: Severity.ERROR,
    logging.CRITICAL: Severity.CRITICAL,
}

MAX_SELF_MESSAGE_CHARS = 2000


class SelfWatchHandler(logging.Handler):
    """Delivers wakey's own ERROR+ log records into the pipeline."""

    def __init__(self, deliver: Callable[[LogEvent], object]) -> None:
        super().__init__(level=logging.ERROR)
        self._deliver = deliver
        self._delivering = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._delivering:
            return  # recursion guard: never handle from inside handling
        severity = _LEVEL_MAP.get(record.levelno)
        if severity is None:
            return
        message = record.getMessage()[:MAX_SELF_MESSAGE_CHARS]
        material = f"{record.name}|{record.created}|{message}"
        event_id = f"evt-self-{hashlib.sha256(material.encode()).hexdigest()[:16]}"
        event = LogEvent(
            id=event_id,
            ts=utcnow(),
            service="wakey-self",
            environment="prod",
            severity=severity,
            message=message,
            source="self",
        )
        self._delivering = True
        try:
            self._deliver(event)
        except Exception:  # noqa: BLE001 — a failed self-report must never raise
            self.handleError(record)
        finally:
            self._delivering = False
