# SPDX-License-Identifier: Apache-2.0
"""Redaction engine (WF-09 §3, SEC-1): the first pass on every ingest.

Fail-closed by design: a runtime engine error on a field yields
``[REDACTION_FAILURE]``, never the raw text. Custom patterns are validated
at construction (compile + length bound); a pattern that cannot compile is
rejected before it can ever run.
"""

from __future__ import annotations

import re

from wakey.core.models import LogEvent

FAILURE_MARKER = "[REDACTION_FAILURE]"
MAX_PATTERN_LENGTH = 500

# (family, pattern) — applied to every text field at ingest and again pre-prompt.
BUILTIN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("aws_secret", r"(?i)aws.{0,20}?['\"][0-9a-zA-Z/+=]{40}['\"]"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("github_pat", r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    ("openai_key", r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    ("google_api_key", r"\bAIza[0-9A-Za-z_-]{35,}\b"),
    ("google_oauth", r"\bya29\.[0-9A-Za-z_-]+\b"),
    ("slack_token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
    ("bearer", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("url_credentials", r"\b\w+(?:s)?://[^/\s:@\"']+:[^@\s/\"']+@"),
    ("stripe_key", r"\b[skpr]k_(test|live)_[0-9a-zA-Z]{20,}\b"),
    ("pypi_token", r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    ("npm_token", r"\bnpm_[A-Za-z0-9]{30,}\b"),
    ("sendgrid_key", r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("credit_card", r"\b(?:\d[ -]?){13,19}\b"),
)


def luhn_valid(digits: str) -> bool:
    """Luhn checksum for credit-card candidates (cuts false positives)."""
    compact = digits.replace(" ", "").replace("-", "")
    if not compact.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(compact)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


class RedactionEngine:
    """Applies builtin + custom patterns to text; reports which families hit."""

    def __init__(self, extra_patterns: dict[str, str] | None = None) -> None:
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            (family, re.compile(pattern)) for family, pattern in BUILTIN_PATTERNS
        ]
        self._custom: set[str] = set()
        for family, pattern in (extra_patterns or {}).items():
            if len(pattern) > MAX_PATTERN_LENGTH:
                raise ValueError(f"redact pattern too long: {family}")
            try:
                self._patterns.append((family, re.compile(pattern)))
                self._custom.add(family)
            except re.error as exc:
                raise ValueError(f"invalid redact regex ({family}): {exc}") from exc
        self._card_pattern = dict(self._patterns)["credit_card"]

    def redact(self, text: str) -> tuple[str, list[str]]:
        """Return (redacted text, hit families). Fail-closed on any engine error."""
        hits: list[str] = []
        try:
            for family, compiled in self._patterns:
                if family == "credit_card":
                    text = self._redact_cards(text, hits)
                    continue
                if compiled.search(text):
                    text = compiled.sub(f"[REDACTED:{family}]", text)
                    hits.append(f"custom:{family}" if family in self._custom else family)
        except Exception:  # noqa: BLE001 — fail-closed means *any* engine error destroys text
            return FAILURE_MARKER, ["engine_failure"]
        return text, hits

    def _redact_cards(self, text: str, hits: list[str]) -> str:
        """Redact digit runs only when Luhn-valid (plain ids must survive)."""
        count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            if luhn_valid(match.group(0)):
                count += 1
                return "[REDACTED:credit_card]"
            return match.group(0)

        result = self._card_pattern.sub(replace, text)
        if count:
            hits.append("credit_card")
        return result


def redact_text(text: str, engine: RedactionEngine) -> str:
    """Redact one externally supplied string; fail-closed to the marker."""
    return engine.redact(text)[0]


def sanitize_log_event(event: LogEvent, engine: RedactionEngine) -> LogEvent:
    """Second-pass sanitization of every externally supplied field (R-04).

    Contract: *no* string that came from outside may reach durable storage
    or a model prompt unredacted — message, trace/request ids, attribute
    keys and values included. The first pass (whole-payload, multiline-
    capable) runs at the ingest boundary; this pass runs per normalized
    event so fields split out of structured payloads are covered too.
    """

    message, _ = engine.redact(event.message)
    trace_id = engine.redact(event.trace_id)[0] if event.trace_id else None
    request_id = engine.redact(event.request_id)[0] if event.request_id else None
    attributes = {
        engine.redact(key)[0]: engine.redact(value)[0] for key, value in event.attributes.items()
    }
    return event.model_copy(
        update={
            "message": message,
            "trace_id": trace_id,
            "request_id": request_id,
            "attributes": attributes,
        }
    )
