# SPDX-License-Identifier: Apache-2.0
"""GCP Pub/Sub push envelope + OIDC token verification (WF-02 §3, E4-T4).

A Pub/Sub push delivers an envelope with a base64 body and (when
configured) an OIDC token in ``Authorization: Bearer <jwt>``. Signature
verification is pluggable: the default :class:`JwtVerifier` delegates to
a JWKS source (Google's endpoint) and requires the ``cryptography``
package — imported lazily so installs without it keep everything else
working. Claims validation (issuer, audience, expiry, authorized
service-account email) is enforced here and is testable without network.
"""

from __future__ import annotations

import base64
import binascii
import importlib.util
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
CLOCK_SKEW_SECONDS = 30


class OidcVerificationError(Exception):
    """The push's OIDC token failed verification; the push must be rejected."""


class SignatureVerifier(Protocol):
    """Verifies a JWS signature against a trusted key source."""

    def verify(self, token: str) -> dict[str, Any]:
        """Return verified claims or raise :class:`OidcVerificationError`."""
        ...


def decode_pubsub_envelope(body: bytes) -> tuple[str, str]:
    """Return (decoded payload, message_id) from a Pub/Sub push envelope.

    Raises :class:`OidcVerificationError`-family ``ValueError`` on
    malformed envelopes; callers dead-letter those.
    """
    try:
        envelope = json.loads(body.decode("utf-8", errors="replace"))
        message = envelope["message"]
        data = base64.b64decode(message["data"], validate=True)
        message_id = str(message.get("messageId", ""))
    except (json.JSONDecodeError, KeyError, TypeError, binascii.Error, ValueError) as exc:
        raise ValueError(f"malformed pub/sub envelope: {exc}") from exc
    if not message_id:
        raise ValueError("malformed pub/sub envelope: missing messageId")
    return data.decode("utf-8", errors="replace"), message_id


def verify_oidc_claims(
    claims: dict[str, Any],
    *,
    audience: str,
    authorized_service_account: str | None = None,
    now: float | None = None,
) -> None:
    """Enforce the claims that matter for a Pub/Sub push (E4-T4).

    issuer: Google; audience: this instance's expected token audience;
    expiry: with a small clock-skew allowance; and (when configured) the
    pushing service account's email. Raises on the first violation.
    """
    now = now if now is not None else time.time()
    issuer = str(claims.get("iss", ""))
    if issuer not in GOOGLE_ISSUERS:
        raise OidcVerificationError(f"untrusted issuer {issuer!r}")
    if str(claims.get("aud", "")) != audience:
        raise OidcVerificationError("audience mismatch")
    expiry = claims.get("exp")
    if not isinstance(expiry, (int, float)) or now - CLOCK_SKEW_SECONDS > float(expiry):
        raise OidcVerificationError("token expired")
    email = claims.get("email")
    if authorized_service_account and email != authorized_service_account:
        raise OidcVerificationError("service account not authorized for this instance")


@dataclass(frozen=True)
class JwtVerifier:
    """Verifies Google-issued JWS tokens against Google's JWKS endpoint.

    Requires the ``cryptography`` package (lazy import — optional
    dependency, THIRD-PARTY-NOTICES review applies).
    """

    audience: str
    authorized_service_account: str | None = None
    jwks_url: str = GOOGLE_JWKS_URL

    def verify(self, token: str) -> dict[str, Any]:

        if importlib.util.find_spec("cryptography") is None:  # pragma: no cover
            raise OidcVerificationError("OIDC verification requires the 'cryptography' package")
        header, payload, signature, signing_input = self._split_token(token)
        key = self._matching_key(header)
        _verify_rs256(key, signing_input, signature)
        claims: dict[str, Any] = json.loads(payload)
        verify_oidc_claims(
            claims,
            audience=self.audience,
            authorized_service_account=self.authorized_service_account,
        )
        return claims

    def _split_token(self, token: str) -> tuple[dict[str, Any], bytes, bytes, bytes]:
        parts = token.split(".")
        if len(parts) != 3:
            raise OidcVerificationError("malformed JWT")
        try:
            header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
            payload = base64.urlsafe_b64decode(parts[1] + "==")
            signature = base64.urlsafe_b64decode(parts[2] + "==")
        except (binascii.Error, json.JSONDecodeError, ValueError) as exc:
            raise OidcVerificationError(f"malformed JWT: {exc}") from exc
        if header.get("alg") != "RS256":
            raise OidcVerificationError("only RS256 is accepted")
        return header, payload, signature, f"{parts[0]}.{parts[1]}".encode()

    def _matching_key(self, header: dict[str, Any]) -> Any:
        import httpx  # noqa: PLC0415 — optional path, imported on verification only
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415

        kid = header.get("kid")
        jwks = httpx.get(self.jwks_url, timeout=10).json()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid and key.get("kty") == "RSA":
                n = int.from_bytes(base64.urlsafe_b64decode(key["n"] + "=="), "big")
                e = int.from_bytes(base64.urlsafe_b64decode(key["e"] + "=="), "big")
                return rsa.RSAPublicNumbers(e, n).public_key()
        raise OidcVerificationError("no matching JWKS key")


def _verify_rs256(public_key: Any, signing_input: bytes, signature: bytes) -> None:
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import padding  # noqa: PLC0415

    try:
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise OidcVerificationError("signature verification failed") from exc
