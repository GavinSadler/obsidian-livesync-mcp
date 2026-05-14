"""Tests for encryption/compression round-trips."""

from __future__ import annotations

import pytest

from obsidian_livesync_mcp.livesync.encryption import (
    COMPRESSION_MARKER,
    ENCRYPTION_MARKER_V1,
    ENCRYPTION_MARKER_V2,
    is_compressed,
    is_encrypted,
)


def test_marker_detection() -> None:
    assert is_encrypted(ENCRYPTION_MARKER_V1 + "payload")
    assert is_encrypted(ENCRYPTION_MARKER_V2 + "payload")
    assert not is_encrypted("plain payload")

    assert is_compressed(COMPRESSION_MARKER + "payload")
    assert not is_compressed("plain payload")


@pytest.mark.skip(reason="encrypt/decrypt not implemented yet")
def test_encrypt_decrypt_roundtrip(sample_passphrase: str) -> None:
    from obsidian_livesync_mcp.livesync.encryption import decrypt, encrypt

    plaintext = "secret data"
    ciphertext = encrypt(plaintext, sample_passphrase)
    assert ciphertext != plaintext
    assert is_encrypted(ciphertext)
    assert decrypt(ciphertext, sample_passphrase) == plaintext


@pytest.mark.skip(reason="compress/decompress not implemented yet")
def test_compress_decompress_roundtrip() -> None:
    from obsidian_livesync_mcp.livesync.encryption import compress, decompress

    plaintext = "a" * 1000
    compressed = compress(plaintext)
    assert is_compressed(compressed)
    assert decompress(compressed) == plaintext
