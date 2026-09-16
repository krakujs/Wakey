# SPDX-License-Identifier: Apache-2.0
"""Security corpus: redaction must catch every secret shape (SEC-1, merge-blocking)."""

from __future__ import annotations

import pytest

from wakey.security.redaction import RedactionEngine

SECRETS = {
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "github_token": "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
    "github_pat": "github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz1234",
    "openai_key": "sk-proj416ae7d8b21c43f98b9c11ad503fdb19",
    "google_api_key": "AIzaSyA-1234567890abcdefghijklmnopqrstuvw",
    "google_oauth": "ya29.a0AfB_byABCdefgh1234567890abcdefghij",
    "slack_token": "xoxb-123456789012-abcdefghijklmnopqrst",
    "jwt": (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
    ),
    "bearer": "Authorization: Bearer abcdef1234567890abcdef1234567890",
    "private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    ),
    "url_credentials": "postgres://admin:s3cr3tpw@db.internal:5432/wakey",
    "email": "incident-owner@acme-corp.io",
    "credit_card": "4111 1111 1111 1111",
}


@pytest.mark.parametrize("family", sorted(SECRETS))
def test_builtin_secret_is_redacted(family: str) -> None:
    engine = RedactionEngine()
    secret = SECRETS[family]
    text = f"error processing request with {secret} in context"
    redacted, hits = engine.redact(text)
    assert secret not in redacted, family
    assert "[REDACTED:" in redacted, family
    assert hits, family


def test_hit_family_is_reported() -> None:
    _, hits = RedactionEngine().redact(SECRETS["aws_access_key"])
    assert hits == ["aws_access_key"]


def test_luhn_rejects_non_card_digit_runs() -> None:
    engine = RedactionEngine()
    text = "request id 1234567890123456 processed"  # passes shape, fails Luhn
    redacted, hits = engine.redact(text)
    assert "1234567890123456" in redacted
    assert "credit_card" not in hits


def test_valid_card_without_spaces_redacted() -> None:
    engine = RedactionEngine()
    redacted, _ = engine.redact("card 4111111111111111 declined")
    assert "4111111111111111" not in redacted


def test_custom_patterns_applied_and_validated() -> None:
    engine = RedactionEngine(extra_patterns={"acme_token": r"\bACME_[A-Z0-9]{16}\b"})
    redacted, hits = engine.redact("token ACME_ABCDEFGHIJKLMNOP leaked")
    assert "ACME_ABCDEFGHIJKLMNOP" not in redacted
    assert hits == ["custom:acme_token"]

    with pytest.raises(ValueError, match="invalid redact regex"):
        RedactionEngine(extra_patterns={"bad": "([a-z+"})
    with pytest.raises(ValueError, match="too long"):
        RedactionEngine(extra_patterns={"long": "a" * 501})


def test_fail_closed_on_engine_error() -> None:
    class ExplodingPattern:
        def search(self, text: str) -> object:
            raise RuntimeError("simulated engine crash")

    engine = RedactionEngine()
    engine._patterns = [("aws_access_key", ExplodingPattern())]  # type: ignore[assignment]
    redacted, hits = engine.redact("anything at all")
    assert redacted == "[REDACTION_FAILURE]"
    assert hits == ["engine_failure"]


def test_redaction_is_idempotent() -> None:
    engine = RedactionEngine()
    once, _ = engine.redact("mail me at oncall@acme.io about sk-abcdefghijklmnop12")
    twice, _ = engine.redact(once)
    assert twice == once
