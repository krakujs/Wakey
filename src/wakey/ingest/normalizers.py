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


def _stable_event_id(service: str, source: str, line: str) -> str:
    """Deterministic id when the payload carries none (idempotent replay)."""
    digest = hashlib.sha256(f"{service}|{source}|{line}".encode()).hexdigest()[:16]
    return f"evt-{digest}"


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


def _event_from_json(
    data: dict[str, Any], service: str, environment: str, source: str, fallback_ts: datetime
) -> LogEvent:
    message = _first_key(data, _MESSAGE_KEYS)
    if message is None or not str(message).strip():
        message = json.dumps(data)
    severity_raw = _first_key(data, _SEVERITY_KEYS)
    severity = map_severity(severity_raw) if severity_raw is not None else Severity.INFO
    event_id = _first_key(data, ("id", "event_id")) or _stable_event_id(
        service, source, json.dumps(data, sort_keys=True)
    )
    attributes = {
        k: str(v)
        for k, v in data.items()
        if k not in _MESSAGE_KEYS + _SEVERITY_KEYS + _TIMESTAMP_KEYS
    }
    return LogEvent(
        id=str(event_id),
        ts=_parse_timestamp(_first_key(data, _TIMESTAMP_KEYS), fallback_ts),
        service=service,
        environment=environment,
        severity=severity,
        message=str(message),
        source=source,
        trace_id=_first_key(data, _TRACE_KEYS) and str(_first_key(data, _TRACE_KEYS)),
        request_id=_first_key(data, _REQUEST_KEYS) and str(_first_key(data, _REQUEST_KEYS)),
        attributes=attributes,
    )


def _looks_like_json(line: str) -> bool:
    return line.startswith("{") or line.startswith("[")


def _parse_logfmt_line(line: str) -> dict[str, str] | None:
    tokens = _LOGFMT_TOKEN.findall(line)
    if len(tokens) < 2:
        return None
    data = {key: value.strip('"') for key, value in tokens}
    prefix = _LOGFMT_TOKEN.sub("", line, count=1).strip()
    if prefix and "message" not in data and "msg" not in data:
        data["message"] = prefix.split("=", 1)[0].strip() or prefix
    return data


def parse_events(
    raw: str,
    service: str,
    environment: str,
    source: str,
    fallback_ts: datetime | None = None,
) -> ParseOutcome:
    """Normalize a raw payload (any mix of JSON lines, logfmt, plain text)."""
    outcome = ParseOutcome()
    fallback = fallback_ts or utcnow()

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > _DEAD_LETTER_MAX:
            outcome.dead_letters.append((stripped[:200], f"line exceeds {_DEAD_LETTER_MAX} bytes"))
            continue
        if _looks_like_json(stripped):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                outcome.dead_letters.append((stripped, f"unparseable JSON: {exc.msg}"))
                continue
            if isinstance(data, dict):
                outcome.events.append(
                    _event_from_json(data, service, environment, source, fallback)
                )
            else:
                outcome.dead_letters.append((stripped, "JSON must be an object"))
            continue
        logfmt = _parse_logfmt_line(stripped)
        if logfmt is not None:
            outcome.events.append(_event_from_json(logfmt, service, environment, source, fallback))
            continue
        severity = Severity.ERROR if stripped.startswith("Traceback") else Severity.INFO
        if re.search(r"\b(ERROR|CRITICAL|FATAL|PANIC)\b", stripped):
            severity = Severity.ERROR
        outcome.events.append(
            LogEvent(
                id=_stable_event_id(service, source, stripped),
                ts=fallback,
                service=service,
                environment=environment,
                severity=severity,
                message=stripped,
                source=source,
            )
        )
    return outcome
