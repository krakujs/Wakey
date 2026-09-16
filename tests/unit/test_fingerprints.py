# SPDX-License-Identifier: Apache-2.0
"""Property-style tests: fingerprint stability under volatile-token churn (DET-3 AC)."""

from __future__ import annotations

from datetime import UTC, datetime

from wakey.core.models import LogEvent, Severity
from wakey.fingerprints.engine import (
    fingerprint_from_event,
    normalize_template,
    parse_python_traceback,
)


def make_event(message: str, service: str = "payments-api", environment: str = "prod") -> LogEvent:
    return LogEvent(
        id=f"evt-{abs(hash(message)) % 10**8:08d}",
        ts=datetime.now(UTC),
        service=service,
        environment=environment,
        severity=Severity.ERROR,
        message=message,
        source="webhook",
    )


def test_template_absorbs_volatile_tokens() -> None:
    template = normalize_template("db down for 10.0.0.3:5432 after 2.5s (req 7)")
    assert template == "db down for {ip} after {dec}s (req {num})"


def test_same_error_churned_ids_same_fingerprint() -> None:
    error_a = (
        "Connection refused to payments-db:5432 for order 84d8e79a-1234-4c0d-9a5f-2b6f9f5f1a33"
    )
    error_b = (
        "Connection refused to payments-db:5433 for order f0e1d2c3-9876-4abc-8def-1122334455aa"
    )
    assert (
        fingerprint_from_event(make_event(error_a)).fp_hash
        == fingerprint_from_event(make_event(error_b)).fp_hash
    )


def test_service_and_environment_scope_fingerprints() -> None:
    error = "Connection refused to db:5432"
    prod = fingerprint_from_event(make_event(error))
    staging = fingerprint_from_event(make_event(error, environment="staging"))
    other = fingerprint_from_event(make_event(error, service="checkout-web"))
    assert prod.fp_hash != staging.fp_hash
    assert prod.fp_hash != other.fp_hash


def test_different_errors_differ() -> None:
    assert (
        fingerprint_from_event(make_event("TypeError boom")).fp_hash
        != fingerprint_from_event(make_event("KeyError boom")).fp_hash
    )


def test_python_traceback_parsed_into_frames() -> None:
    traceback_text = (
        "Traceback (most recent call last):\n"
        '  File "app/api.py", line 42, in handler\n'
        "    result = charge(order)\n"
        '  File "app/billing.py", line 87, in charge\n'
        '    return gateway.refund(order.metadata["key"])\n'
        "TypeError: 'NoneType' object is not subscriptable\n"
    )
    parsed = parse_python_traceback(traceback_text)
    assert parsed is not None
    message, frames = parsed
    assert message == "TypeError: 'NoneType' object is not subscriptable"
    assert frames[-1].path == "app/billing.py"
    assert frames[-1].line == 87

    fingerprint = fingerprint_from_event(make_event(traceback_text))
    assert fingerprint.frames[-1].path == "app/billing.py"
    assert fingerprint.template.startswith("TypeError:")


def test_plain_message_is_not_a_traceback() -> None:
    assert parse_python_traceback("Connection refused to db:5432") is None
