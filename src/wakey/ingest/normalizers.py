# SPDX-License-Identifier: Apache-2.0
"""Log normalizers (WF-02 §4, DET-1): platform payloads → ``LogEvent``.

Format auto-detection per line: JSON object → structured event; logfmt
(≥2 ``key=value`` tokens) → structured; anything else → plain text with
heuristic severity. Lines that *look* like JSON but fail to parse are
dead-lettered with a reason — never silently mangled into plain text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from wakey.core.models import LogEvent, Severity, utcnow

_TIMESTAMP_KEYS = ("timestamp", "time", "ts", "@timestamp")
_MESSAGE_KEYS = ("message", "msg", "error", "event")
_SEVERITY_KEYS = ("level", "severity", "lvl")
_TRACE_KEYS = ("trace_id", "traceId", "trace")
_REQUEST_KEYS = ("request_id", "requestId", "request")

_LOGFMT_TOKEN = re.compile(r"(\w+)=((?:\"[^\"]*\")|(?:\S+))")
_DEAD_LETTER_MAX = 64 * 1024  # oversized "lines" are binary garbage, not logs

_SYSLOG_SEVERITY = {
    0: Severity.CRITICAL,
    1: Severity.CRITICAL,
    2: Severity.CRITICAL,
    3: Severity.ERROR,
    4: Severity.WARNING,
    5: Severity.INFO,
    6: Severity.INFO,
    7: Severity.DEBUG,
}
_TEXT_SEVERITY = {
    "fatal": Severity.CRITICAL,
    "critical": Severity.CRITICAL,
    "crit": Severity.CRITICAL,
    "panic": Severity.CRITICAL,
    "emerg": Severity.CRITICAL,
    "error": Severity.ERROR,
    "err": Severity.ERROR,
    "exception": Severity.ERROR,
    "warning": Severity.WARNING,
    "warn": Severity.WARNING,
    "notice": Severity.INFO,
    "info": Severity.INFO,
    "debug": Severity.DEBUG,
    "trace": Severity.DEBUG,
}


@dataclass(frozen=True)
class EventContext:
    """Everything that stays constant while normalizing one payload."""

    service: str
    environment: str
    source: str
    delivery_id: str = "delivery"
    fallback_ts: datetime | None = None


@dataclass
class ParseOutcome:
    """Events ready for the pipeline + lines that need the dead-letter path."""

    events: list[LogEvent] = field(default_factory=list)
    dead_letters: list[tuple[str, str]] = field(default_factory=list)  # (line, reason)


def map_severity(raw: str | int) -> Severity:
    """Map platform severities (text or syslog 0-7) to internal severity."""
    if isinstance(raw, int):
        return _SYSLOG_SEVERITY.get(max(0, min(raw, 7)), Severity.INFO)
    return _TEXT_SEVERITY.get(raw.strip().lower(), Severity.INFO)


def _stable_event_id(service: str, source: str, delivery_id: str, index: int, line: str) -> str:
    """Deterministic per-delivery id: same delivery replayed -> same ids (WF-02 §7)."""
    material = f"{service}|{source}|{delivery_id}|{index}|{line}"
    return f"evt-{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def _parse_timestamp(raw: object, fallback: datetime) -> datetime:
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            return fallback
    return fallback


def _first_key(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _event_from_json(data: dict[str, Any], ctx: EventContext, index: int) -> LogEvent:
    """Build one event from a structured mapping.

    Raises ``ValueError`` on fields that cannot be coerced — the caller
    dead-letters that line and keeps processing valid siblings (R-02).
    """
    message = _first_key(data, _MESSAGE_KEYS)
    if message is None or not str(message).strip():
        message = json.dumps(data)
    severity_raw = _first_key(data, _SEVERITY_KEYS)
    if severity_raw is not None and not isinstance(severity_raw, (str, int)):
        raise ValueError(f"severity field must be text or int, got {type(severity_raw).__name__}")
    severity = map_severity(severity_raw) if severity_raw is not None else Severity.INFO
    canonical = json.dumps(data, sort_keys=True)
    event_id = _first_key(data, ("id", "event_id")) or _stable_event_id(
        ctx.service, ctx.source, ctx.delivery_id, index, canonical
    )
    try:
        attributes = {
            k: str(v)
            for k, v in data.items()
            if k not in _MESSAGE_KEYS + _SEVERITY_KEYS + _TIMESTAMP_KEYS
        }
        return LogEvent(
            id=str(event_id),
            ts=_parse_timestamp(_first_key(data, _TIMESTAMP_KEYS), ctx.fallback_ts or utcnow()),
            service=ctx.service,
            environment=ctx.environment,
            severity=severity,
            message=str(message),
            source=ctx.source,
            trace_id=(str(v) if (v := _first_key(data, _TRACE_KEYS)) is not None else None),
            request_id=(str(v) if (v := _first_key(data, _REQUEST_KEYS)) is not None else None),
            attributes=attributes,
        )
    except ValidationError as exc:
        raise ValueError(f"invalid event field: {exc}") from exc


def _looks_like_json(line: str) -> bool:
    # Objects only: log lines beginning with "[" (plain text, redaction
    # markers, arrays) are legitimate non-JSON content and must flow to
    # the plain-text path, not the dead-letter path.
    return line.startswith("{")


def _parse_logfmt_line(line: str) -> dict[str, str] | None:
    tokens = _LOGFMT_TOKEN.findall(line)
    if len(tokens) < 2:
        return None
    data = {key: value.strip('"') for key, value in tokens}
    prefix = _LOGFMT_TOKEN.sub("", line, count=1).strip()
    if prefix and "message" not in data and "msg" not in data:
        data["message"] = prefix
    return data


_TRACEBACK_HEAD = re.compile(r"^Traceback \(most recent call last\):")
_INDENTED_FRAME = re.compile(r"^\s")


def _reassemble_traceback(lines: list[str], start: int) -> tuple[str, int]:
    """Consume one traceback block starting at ``start``; return (block, next index).

    Plain-text logs arrive line-by-line, but both the traceback parser and
    multiline redaction (a PEM block inside an exception, say) need the
    block as one message (R-04). Shape: the ``Traceback (...)`` head, the
    indented ``File ...`` frames, and the first following non-indented
    exception line.
    """
    block = [lines[start]]
    index = start + 1
    while index < len(lines) and _INDENTED_FRAME.match(lines[index]):
        block.append(lines[index])
        index += 1
    if index < len(lines) and not _TRACEBACK_HEAD.match(lines[index]):
        block.append(lines[index])  # the exception line closes the block
        index += 1
    return "\n".join(block), index


def parse_events(raw: str, ctx: EventContext) -> ParseOutcome:
    """Normalize a raw payload (any mix of JSON lines, logfmt, plain text)."""
    outcome = ParseOutcome()
    fallback = ctx.fallback_ts or utcnow()
    lines = raw.splitlines()

    index = 0
    while index < len(lines):
        line_number = index
        index += 1
        stripped = lines[line_number].strip()
        if not stripped:
            continue
        if len(stripped) > _DEAD_LETTER_MAX:
            outcome.dead_letters.append((stripped[:200], f"line exceeds {_DEAD_LETTER_MAX} bytes"))
            continue
        if _TRACEBACK_HEAD.match(stripped):
            # back up: the block reassembler owns lines from the head onward
            block, index = _reassemble_traceback(lines, line_number)
            outcome.events.append(_plain_text_event(block, ctx, line_number, fallback))
            continue
        if _looks_like_json(stripped):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                outcome.dead_letters.append((stripped, f"unparseable JSON: {exc.msg}"))
                continue
            if isinstance(data, dict):
                try:
                    outcome.events.append(_event_from_json(data, ctx, line_number))
                except ValueError as exc:
                    outcome.dead_letters.append((stripped[:200], str(exc)))
            else:
                outcome.dead_letters.append((stripped, "JSON must be an object"))
            continue
        logfmt = _parse_logfmt_line(stripped)
        if logfmt is not None:
            try:
                outcome.events.append(_event_from_json(logfmt, ctx, line_number))
            except ValueError as exc:
                outcome.dead_letters.append((stripped[:200], str(exc)))
            continue
        outcome.events.append(_plain_text_event(stripped, ctx, line_number, fallback))
    return outcome


def _plain_text_event(message: str, ctx: EventContext, index: int, fallback: datetime) -> LogEvent:
    severity = Severity.ERROR if _TRACEBACK_HEAD.match(message) else Severity.INFO
    if re.search(r"\b(ERROR|CRITICAL|FATAL|PANIC)\b", message):
        severity = Severity.ERROR
    return LogEvent(
        id=_stable_event_id(ctx.service, ctx.source, ctx.delivery_id, index, message.strip()),
        ts=fallback,
        service=ctx.service,
        environment=ctx.environment,
        severity=severity,
        message=message,
        source=ctx.source,
    )
