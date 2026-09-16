# SPDX-License-Identifier: Apache-2.0
"""Structured logging: JSON lines with request-id correlation (eng-standards §5).

Kept deliberately small: stdlib logging + a JSON formatter + a contextvar the
request middleware binds. No third-party logging framework.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar

REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    """Render a log record as one JSON object with request correlation."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": REQUEST_ID.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def new_request_id() -> str:
    return uuid.uuid4().hex


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once (idempotent for repeated calls in tests)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
