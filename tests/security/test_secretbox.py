# SPDX-License-Identifier: Apache-2.0
"""SecretBox envelope security tests (SEC-4/E10-T3, readiness #8).

Direct coverage of the master-key envelope: round-trip secrecy, tamper
rejection, wrong-key failure, legacy passthrough, malformed tokens, and
key-validation fail-closed rules. The engineering-standards critical
module floor (§ security package ≥ 95%) is enforced against this file.
"""

from __future__ import annotations

import base64

import pytest

from wakey.security.secretbox import (
    PREFIX,
    SecretBox,
    SecretBoxError,
    hash_token,
    new_master_key,
)

KEY = new_master_key()
SECRET = "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----"


def test_new_master_key_is_256_bit_hex() -> None:
    key = new_master_key()
    assert len(key) == 64  # 32 bytes, hex-encoded
    assert bytes.fromhex(key)
    assert key != new_master_key(), "keys must never repeat"


def test_encrypt_decrypt_round_trip() -> None:
    box = SecretBox(KEY)
    token = box.encrypt(SECRET)
    assert token.startswith(PREFIX)
    assert SECRET not in token, "ciphertext must not leak the plaintext"
    assert box.decrypt(token) == SECRET


def test_each_encryption_uses_a_fresh_nonce() -> None:
    box = SecretBox(KEY)
    tokens = {box.encrypt(SECRET) for _ in range(8)}
    assert len(tokens) == 8, "nonce reuse would break AES-GCM"


def test_tampered_ciphertext_fails_authentication() -> None:
    box = SecretBox(KEY)
    token = box.encrypt(SECRET)
    nonce_b64, sealed_b64 = token[len(PREFIX) :].split(":", 1)
    sealed = bytearray(base64.b64decode(sealed_b64))
    sealed[0] ^= 0xFF  # flip one ciphertext bit
    tampered = f"{PREFIX}{nonce_b64}:{base64.b64encode(bytes(sealed)).decode()}"
    with pytest.raises(SecretBoxError, match="wrong key or tampered"):
        box.decrypt(tampered)


def test_wrong_key_never_decrypts() -> None:
    token = SecretBox(KEY).encrypt(SECRET)
    with pytest.raises(SecretBoxError):
        SecretBox(new_master_key()).decrypt(token)


def test_legacy_plaintext_passthrough() -> None:
    box = SecretBox(KEY)
    assert box.decrypt("plain-old-value") == "plain-old-value"


def test_malformed_envelopes_raise_secretbox_error() -> None:
    box = SecretBox(KEY)
    for bad in (
        PREFIX,  # no payload at all
        PREFIX + "not-base64:also-not",  # undecodable
        PREFIX + base64.b64encode(b"nonce").decode(),  # missing sealed-part separator
    ):
        with pytest.raises(SecretBoxError):
            box.decrypt(bad)


@pytest.mark.parametrize("bad_key", ["", "   ", "nothex", "aabb", "00" * 16])  # < 32 bytes
def test_key_validation_fails_closed(bad_key: str) -> None:
    with pytest.raises(ValueError):
        SecretBox(bad_key)


def test_hash_token_is_one_way_and_deterministic() -> None:
    assert hash_token("session-1") == hash_token("session-1")
    assert hash_token("session-1") != hash_token("session-2")
    assert len(hash_token("session-1")) == 64
    assert "session-1" not in hash_token("session-1")


def test_is_available_reflects_cryptography_install() -> None:
    assert isinstance(SecretBox.is_available(), bool)
