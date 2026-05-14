"""Encryption and compression for chunk payloads.

LiveSync supports:
  - V2 (HKDF) — header marker `%=`, modern key derivation.
  - V1 (PBKDF2) — header marker `%`, legacy.
  - Deflate compression — marker `~`, applied before encryption.

Reference: src/lib/src/pouchdb/encryption.ts
Salt constant: `SALT_OF_PASSPHRASE = "rHGMPtr6oWw7VSa3W3wpa8fT8U"`
"""

from __future__ import annotations

SALT_OF_PASSPHRASE = "rHGMPtr6oWw7VSa3W3wpa8fT8U"

ENCRYPTION_MARKER_V1 = "%"
ENCRYPTION_MARKER_V2 = "%="
COMPRESSION_MARKER = "~"


def encrypt(plaintext: str, passphrase: str) -> str:
    """Encrypt with V2 HKDF and return the wire-format ciphertext."""
    raise NotImplementedError


def decrypt(ciphertext: str, passphrase: str) -> str:
    """Auto-detect V1 / V2 from the marker and decrypt."""
    raise NotImplementedError


def compress(data: str) -> str:
    """Deflate-compress and prepend the `~` marker."""
    raise NotImplementedError


def decompress(data: str) -> str:
    """Inverse of compress. Pass-through if no `~` marker."""
    raise NotImplementedError


def is_encrypted(payload: str) -> bool:
    return payload.startswith(ENCRYPTION_MARKER_V1) or payload.startswith(ENCRYPTION_MARKER_V2)


def is_compressed(payload: str) -> bool:
    return payload.startswith(COMPRESSION_MARKER)
