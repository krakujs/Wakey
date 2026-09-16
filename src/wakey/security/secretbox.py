# SPDX-License-Identifier: Apache-2.0
"""Encrypted-at-rest credentials (SEC-4, E10-T3): the master-key envelope.

``SecretBox`` encrypts reversible secrets (GitHub App private keys,
webhook secrets) with AES-256-GCM under a key derived from
``WAKEY_MASTER_KEY``. Ciphertext tokens carry a version prefix
(``enc1:``) so plaintext values written before encryption was configured
still decrypt (passthrough) — and so enabling the master key later
migrates values on first rewrite rather than breaking reads.

Fail-closed rules: an empty master key means no encryption — callers that
hold *high-value* credentials (app private keys) must refuse to store
them unencrypted rather than fall back. Tampering with ciphertext fails
authentication on decrypt and yields :class:`SecretBoxError`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from typing import Any

PREFIX = "enc1:"


class SecretBoxError(Exception):
    """Decryption failed (wrong key, tampered ciphertext, or malformed token)."""


def new_master_key() -> str:
    """Generate a 256-bit master key as hex (the .env.example recipe)."""
    return os.urandom(32).hex()


def hash_token(token: str) -> str:
    """One-way hash for verify-only secrets (sessions, setup tokens).

    Unlike :class:`SecretBox` these never need the raw value back, so a
    database dump cannot be replayed to authenticate.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class SecretBox:
    """AES-256-GCM envelope encryption for stored secrets."""

    def __init__(self, master_key_hex: str) -> None:
        if not master_key_hex or not master_key_hex.strip():
            raise ValueError("master key must not be empty")
        try:
            raw = bytes.fromhex(master_key_hex.strip())
        except ValueError as exc:
            raise ValueError("master key must be hex") from exc
        if len(raw) < 32:
            raise ValueError("master key must be at least 32 bytes (256 bits)")
        self._key = hashlib.sha256(raw + b"wakey-secretbox-v1").digest()
        self._aesgcm = self._load_aesgcm()

    @staticmethod
    def is_available() -> bool:
        import importlib.util  # noqa: PLC0415 — optional-dependency probe

        return importlib.util.find_spec("cryptography") is not None

    def _load_aesgcm(self) -> Any:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        return AESGCM(self._key)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        sealed = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
        return f"{PREFIX}{base64.b64encode(nonce).decode()}:{base64.b64encode(sealed).decode()}"

    def decrypt(self, token: str) -> str:
        if not token.startswith(PREFIX):
            return token  # legacy plaintext written before encryption was enabled
        try:
            nonce_b64, sealed_b64 = token[len(PREFIX) :].split(":", 1)
            nonce = base64.b64decode(nonce_b64)
            sealed = base64.b64decode(sealed_b64)
            decrypted = self._aesgcm.decrypt(nonce, sealed, None)
            return str(decrypted.decode())
        except (binascii.Error, ValueError) as exc:
            raise SecretBoxError(f"malformed secret envelope: {exc}") from exc
        except Exception as exc:  # cryptography raises InvalidTag (auth failure)
            raise SecretBoxError("secret decryption failed (wrong key or tampered)") from exc
