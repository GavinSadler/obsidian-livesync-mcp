"""Tests for encryption / compression round-trips.

The HKDF tests pin the wire-format parameters (PBKDF2 iterations, salt
lengths, IV length, tag length, layout) that have to stay byte-for-byte
identical to the LiveSync plugin to remain interoperable. Changing any
of these constants without updating the plugin spec breaks every
encrypted vault, so the tests assert on them directly.
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from obsidian_livesync_mcp.livesync.encryption import (
    COMPRESSION_MARKER,
    EPHEMERAL_HKDF_PREFIX,
    GCM_TAG_LENGTH,
    HKDF_PREFIX,
    HKDF_SALT_LENGTH,
    IV_LENGTH,
    KEY_LENGTH,
    PBKDF2_SALT_LENGTH,
    V3_PREFIX,
    EncryptionNotSupportedError,
    compress,
    decompress,
    decrypt,
    decrypt_hkdf,
    derive_chunk_key,
    derive_master_key,
    encrypt,
    encrypt_hkdf,
    is_compressed,
    is_encrypted,
    is_hkdf_encrypted,
)


# Locked to the octagonal-wheels spec; do not change without a coordinated
# plugin-side change.
def test_wire_format_constants_match_spec() -> None:
    assert PBKDF2_SALT_LENGTH == 32
    assert HKDF_SALT_LENGTH == 32
    assert IV_LENGTH == 12
    assert GCM_TAG_LENGTH == 16
    assert KEY_LENGTH == 32


def test_marker_detection() -> None:
    assert is_encrypted(HKDF_PREFIX + "payload")
    assert is_encrypted(EPHEMERAL_HKDF_PREFIX + "payload")
    assert is_encrypted(V3_PREFIX + "payload")
    assert is_encrypted("%legacy")
    assert not is_encrypted("plain payload")

    assert is_hkdf_encrypted(HKDF_PREFIX + "x")
    assert is_hkdf_encrypted(EPHEMERAL_HKDF_PREFIX + "x")
    assert not is_hkdf_encrypted("%legacy")
    assert not is_hkdf_encrypted("plain")

    assert is_compressed(COMPRESSION_MARKER + "payload")
    assert not is_compressed("plain payload")


def test_derive_master_key_is_deterministic_and_correct_length() -> None:
    salt = bytes(range(PBKDF2_SALT_LENGTH))
    k1 = derive_master_key("passphrase", salt)
    k2 = derive_master_key("passphrase", salt)
    assert k1 == k2
    assert len(k1) == KEY_LENGTH


def test_derive_master_key_differs_by_passphrase_and_salt() -> None:
    salt_a = bytes(range(PBKDF2_SALT_LENGTH))
    salt_b = bytes(range(PBKDF2_SALT_LENGTH, PBKDF2_SALT_LENGTH * 2))
    assert derive_master_key("a", salt_a) != derive_master_key("b", salt_a)
    assert derive_master_key("a", salt_a) != derive_master_key("a", salt_b)


def test_derive_chunk_key_is_deterministic_and_correct_length() -> None:
    master = b"\x00" * KEY_LENGTH
    salt = bytes(range(HKDF_SALT_LENGTH))
    k1 = derive_chunk_key(master, salt)
    k2 = derive_chunk_key(master, salt)
    assert k1 == k2
    assert len(k1) == KEY_LENGTH


def test_derive_rejects_wrong_salt_length() -> None:
    with pytest.raises(ValueError):
        derive_master_key("p", b"too short")
    with pytest.raises(ValueError):
        derive_chunk_key(b"\x00" * KEY_LENGTH, b"too short")


def test_hkdf_roundtrip_ascii() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt_hkdf("hello, world", "secret", salt)
    assert ct.startswith(HKDF_PREFIX)
    assert decrypt_hkdf(ct, "secret", salt) == "hello, world"


def test_hkdf_roundtrip_unicode() -> None:
    # From octagonal-wheels test fixtures.
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    plaintext = "國破山河在城春草木深-raison d'être-🍔!"
    ct = encrypt_hkdf(plaintext, "test-passphrase", salt)
    assert decrypt_hkdf(ct, "test-passphrase", salt) == plaintext


def test_hkdf_roundtrip_empty_string() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt_hkdf("", "p", salt)
    assert decrypt_hkdf(ct, "p", salt) == ""


def test_hkdf_each_encryption_uses_fresh_iv_and_salt() -> None:
    # Two encryptions of the same plaintext must produce different
    # ciphertexts (fresh IV + HKDF salt each time).
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    a = encrypt_hkdf("payload", "p", salt)
    b = encrypt_hkdf("payload", "p", salt)
    assert a != b


def test_hkdf_layout_matches_spec() -> None:
    # iv (12 bytes) | hkdf_salt (32 bytes) | ciphertext+tag
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt_hkdf("x", "p", salt)
    blob = base64.b64decode(ct[len(HKDF_PREFIX) :])
    assert len(blob) == IV_LENGTH + HKDF_SALT_LENGTH + len(b"x") + GCM_TAG_LENGTH


def test_hkdf_decrypt_wrong_passphrase_raises() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt_hkdf("payload", "right", salt)
    with pytest.raises(InvalidTag):
        decrypt_hkdf(ct, "wrong", salt)


def test_hkdf_decrypt_wrong_salt_raises() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    other = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt_hkdf("payload", "p", salt)
    with pytest.raises(InvalidTag):
        decrypt_hkdf(ct, "p", other)


def test_hkdf_decrypt_rejects_missing_marker() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    with pytest.raises(ValueError):
        decrypt_hkdf("no marker", "p", salt)


def test_hkdf_decrypt_rejects_short_blob() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    with pytest.raises(ValueError):
        decrypt_hkdf(HKDF_PREFIX + base64.b64encode(b"short").decode("ascii"), "p", salt)


def test_dispatch_decrypt_routes_to_hkdf() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt_hkdf("payload", "p", salt)
    assert decrypt(ct, "p", salt) == "payload"


def test_dispatch_decrypt_requires_salt_for_hkdf() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt_hkdf("payload", "p", salt)
    with pytest.raises(ValueError):
        decrypt(ct, "p", None)


def test_dispatch_decrypt_raises_for_legacy_v2() -> None:
    with pytest.raises(EncryptionNotSupportedError):
        decrypt("%abc", "p", b"\x00" * PBKDF2_SALT_LENGTH)


def test_dispatch_decrypt_raises_for_v3() -> None:
    with pytest.raises(EncryptionNotSupportedError):
        decrypt("%~abc", "p", b"\x00" * PBKDF2_SALT_LENGTH)


def test_dispatch_decrypt_rejects_unknown_marker() -> None:
    with pytest.raises(ValueError):
        decrypt("plaintext, no marker", "p")


def test_encrypt_facade_uses_hkdf() -> None:
    salt = os.urandom(PBKDF2_SALT_LENGTH)
    ct = encrypt("payload", "p", salt)
    assert ct.startswith(HKDF_PREFIX)
    assert decrypt(ct, "p", salt) == "payload"


def test_compress_decompress_roundtrip() -> None:
    plaintext = "a" * 1000
    compressed = compress(plaintext)
    assert is_compressed(compressed)
    assert len(compressed) < len(plaintext)
    assert decompress(compressed) == plaintext


def test_compress_handles_unicode() -> None:
    plaintext = "Hello, 世界! 🌍\n" * 50
    assert decompress(compress(plaintext)) == plaintext


def test_decompress_passthrough_for_uncompressed() -> None:
    assert decompress("plain string") == "plain string"
