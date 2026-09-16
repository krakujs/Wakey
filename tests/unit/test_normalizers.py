# SPDX-License-Identifier: Apache-2.0
"""Tests: format auto-detection and field extraction (E4-T2, DET-1)."""

from __future__ import annotations

from wakey.core.models import Severity
from wakey.ingest.normalizers import EventContext, map_severity, parse_events


def test_jsonl_lines_become_structured_events() -> None:
    json_line_1 = (
        '{"timestamp": "2026-09-16T10:00:00Z", "level": "error",'
        ' "message": "db down", "trace_id": "t-1"}'
    )
    raw = "\n".join([json_line_1, '{"level": "info", "msg": "started"}'])
    outcome = parse_events(raw, EventContext(service="api", environment="prod", source="webhook"))
    assert len(outcome.events) == 2
    assert len(outcome.dead_letters) == 0
    first = outcome.events[0]
    assert first.severity is Severity.ERROR
    assert first.message == "db down"
    assert first.trace_id == "t-1"
    assert first.ts.year == 2026


def test_logfmt_detected_and_severity_mapped() -> None:
    raw = 'ts=2026-09-16T10:00:00Z level=warn msg="cache miss rate high" trace_id=t9'
    outcome = parse_events(raw, EventContext(service="api", environment="prod", source="docker"))
    assert len(outcome.events) == 1
    event = outcome.events[0]
    assert event.severity is Severity.WARNING
    assert event.message == "cache miss rate high"
    assert event.trace_id == "t9"


def test_plain_text_lines_with_error_markers() -> None:
    raw = "service started\nERROR: connection refused\nTraceback (most recent call last): ..."
    outcome = parse_events(raw, EventContext(service="api", environment="prod", source="docker"))
    assert [e.severity for e in outcome.events] == [Severity.INFO, Severity.ERROR, Severity.ERROR]
    assert outcome.events[2].message.startswith("Traceback")


def test_broken_json_is_dead_lettered_not_mangled() -> None:
    raw = '{"level": "error", "message": "truncated'
    outcome = parse_events(raw, EventContext(service="api", environment="prod", source="webhook"))
    assert outcome.events == []
    assert len(outcome.dead_letters) == 1
    line, reason = outcome.dead_letters[0]
    assert "unparseable JSON" in reason


def test_empty_lines_skipped_and_ids_stable() -> None:
    raw = '{"msg": "one"}\n\n{"msg": "one"}'
    ctx = EventContext(service="api", environment="prod", source="webhook", delivery_id="d-1")
    outcome = parse_events(raw, ctx)
    assert len(outcome.events) == 2

    # same delivery id replayed -> identical event ids (idempotent processing)
    replay = parse_events(raw, ctx)
    assert replay.events[0].id == outcome.events[0].id
    assert replay.events[1].id != replay.events[0].id  # distinct lines, distinct ids


def test_severity_mapping_table() -> None:
    assert map_severity("FATAL") is Severity.CRITICAL
    assert map_severity(3) is Severity.ERROR  # syslog err
    assert map_severity(7) is Severity.DEBUG
    assert map_severity("whatever") is Severity.INFO
