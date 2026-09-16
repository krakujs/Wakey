# SPDX-License-Identifier: Apache-2.0
"""OIDC envelope tests (E4-T4): claims validation, envelope decoding.

Signature verification against real Google JWKS is a live gate (needs
network + credentials); these tests prove the claims logic and the
envelope decoder, with a stubbed verifier standing in for JWKS.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from wakey.ingest.oidc import (
    OidcVerificationError,
    decode_pubsub_envelope,
    verify_oidc_claims,
)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


NOW = time.time()


def valid_claims(**overrides: object) -> dict:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "wakey-push",
        "exp": NOW + 300,
        "email": "wakey-push@project.iam.gserviceaccount.com",
    }
    claims.update(overrides)
    return claims


def test_envelope_decodes_payload_and_message_id() -> None:
    body = json.dumps(
        {
            "message": {
                "data": b64(json.dumps({"level": "error", "msg": "boom"}).encode()),
                "messageId": "m-1",
            },
            "subscription": "sub-1",
        }
    ).encode()
    payload, message_id = decode_pubsub_envelope(body)
    assert message_id == "m-1"
    assert json.loads(payload)["msg"] == "boom"


def test_malformed_envelopes_rejected() -> None:
    with pytest.raises(ValueError):
        decode_pubsub_envelope(b"{not json")
    no_id = json.dumps({"message": {"data": b64(b"x")}}).encode()
    with pytest.raises(ValueError):
        decode_pubsub_envelope(no_id)
    bad_b64 = json.dumps({"message": {"data": "!!!", "messageId": "m"}}).encode()
    with pytest.raises(ValueError):
        decode_pubsub_envelope(bad_b64)


def test_valid_claims_pass() -> None:
    verify_oidc_claims(
        valid_claims(), audience="wakey-push", authorized_service_account=None, now=NOW
    )


def test_untrusted_issuer_rejected() -> None:
    with pytest.raises(OidcVerificationError, match="issuer"):
        verify_oidc_claims(valid_claims(iss="https://evil.example"), audience="wakey-push", now=NOW)


def test_audience_mismatch_rejected() -> None:
    with pytest.raises(OidcVerificationError, match="audience"):
        verify_oidc_claims(valid_claims(aud="other"), audience="wakey-push", now=NOW)


def test_expired_token_rejected_with_skew_allowance() -> None:
    clearly_expired = valid_claims(exp=NOW - 60)
    with pytest.raises(OidcVerificationError, match="expired"):
        verify_oidc_claims(clearly_expired, audience="wakey-push", now=NOW)
    within_skew = valid_claims(exp=NOW - 10)  # expired 10s ago, inside the 30s skew
    verify_oidc_claims(within_skew, audience="wakey-push", now=NOW)


def test_unauthorized_service_account_rejected() -> None:
    with pytest.raises(OidcVerificationError, match="service account"):
        verify_oidc_claims(
            valid_claims(email="other@project.iam.gserviceaccount.com"),
            audience="wakey-push",
            authorized_service_account="wakey-push@project.iam.gserviceaccount.com",
            now=NOW,
        )
