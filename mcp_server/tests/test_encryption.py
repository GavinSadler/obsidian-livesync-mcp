"""Tests for encryption/compression round-trips."""

from __future__ import annotations

import pytest

from obsidian_livesync_mcp.livesync.encryption import (
    COMPRESSION_MARKER,
    ENCRYPTION_MARKER_V1,
    ENCRYPTION_MARKER_V2,
    EncryptionNotSupportedError,
    compress,
    decompress,
    decrypt,
    encrypt,
    is_compressed,
    is_encrypted,
)


def test_marker_detection() -> None:
    assert is_encrypted(ENCRYPTION_MARKER_V1 + "payload")
    assert is_encrypted(ENCRYPTION_MARKER_V2 + "payload")
    assert not is_encrypted("plain payload")

    assert is_compressed(COMPRESSION_MARKER + "payload")
    assert not is_compressed("plain payload")


def test_encrypt_raises_until_implemented() -> None:
    # MVP doesn't support encryption — should raise a clear error so
    # the user knows to disable E2EE in plugin settings.
    with pytest.raises(EncryptionNotSupportedError):
        encrypt("plaintext", "passphrase")
    with pytest.raises(EncryptionNotSupportedError):
        decrypt("%=ciphertext", "passphrase")


def test_compress_decompress_roundtrip() -> None:
    plaintext = "a" * 1000
    compressed = compress(plaintext)
    assert is_compressed(compressed)
    assert len(compressed) < len(plaintext)  # actually compressed
    assert decompress(compressed) == plaintext


def test_compress_handles_unicode() -> None:
    plaintext = "Hello, 世界! 🌍\n" * 50
    compressed = compress(plaintext)
    assert decompress(compressed) == plaintext


def test_decompress_passthrough_for_uncompressed() -> None:
    assert decompress("plain string") == "plain string"
