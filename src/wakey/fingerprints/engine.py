# SPDX-License-Identifier: Apache-2.0
"""Fingerprint engine (WF-03, DET-3): turn error text into a stable identity.

Two stages:
1. **Templating** — volatile tokens (uuids, ips, hex ids, timestamps, numbers)
   become slots so the same underlying error always yields the same template.
2. **Hashing** — sha256 over service + environment + template + top frames.

Environment scoping is deliberate: the same error in staging and prod are
different fingerprints (DET-12).
"""

from __future__ import annotations

import hashlib
import re

from wakey.core.models import Fingerprint, LogEvent, Severity, TraceFrame, utcnow
from wakey.fingerprints.parsers import parse_traceback

TOP_FRAMES = 3

# Order matters: specific patterns before generic number substitution.
_TEMPLATING: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "uuid",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
    (
        "ts",
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"),
    ),
    ("ip", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")),
    ("dec", re.compile(r"\b\d+\.\d+")),
    ("hex", re.compile(r"\b[0-9a-fA-F]{8,}\b")),
    ("num", re.compile(r"\b\d+\b")),
)


def normalize_template(message: str) -> str:
    """Replace volatile tokens with named slots, leaving the shape intact."""
    result = message
    for slot, pattern in _TEMPLATING:
        result = pattern.sub(f"{{{slot}}}", result)
    return result


def fingerprint_hash(
    service: str, environment: str, template: str, frames: tuple[TraceFrame, ...]
) -> str:
    """Stable 16-hex-char identity for an error family (DET-3)."""
    frame_key = "|".join(f"{f.path}:{f.line}:{f.function}" for f in frames[:TOP_FRAMES])
    material = f"{service}|{environment}|{template}|{frame_key}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def fingerprint_from_event(event: LogEvent) -> Fingerprint:
    """Build the fingerprint identity for an event.

    If the event carries no frames but its message looks like a Python
    traceback, frames and the root error line are extracted first.
    """
    message = event.message
    frames = event.frames
    if not frames:
        parsed = parse_traceback(message)
        if parsed is not None:
            message, frames = parsed
    template = normalize_template(message)
    return Fingerprint(
        fp_hash=fingerprint_hash(event.service, event.environment, template, frames),
        service=event.service,
        environment=event.environment,
        template=template,
        frames=frames[:TOP_FRAMES],
        severity=event.severity,
        last_seen=event.ts or utcnow(),
    )


def looks_like_error(event: LogEvent) -> bool:
    """Cheap pre-filter: only error-severity events can wake the policy gate."""
    return event.severity.rank >= Severity.ERROR.rank
