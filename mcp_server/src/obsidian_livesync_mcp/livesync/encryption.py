"""Encryption and compression for chunk payloads.

LiveSync supports:
  - V2 (HKDF) — header marker `%=`, modern key derivation.
  - V1 (PBKDF2) — header marker `%`, legacy.
  - Deflate compression — marker `~`, applied before encryption.

Reference: src/lib/src/pouchdb/encryption.ts
Salt constant: `SALT_OF_PASSPHRASE = "rHGMPtr6oWw7VSa3W3wpa8fT8U"`
"""

from __future__ import annotations

import base64
import zlib

SALT_OF_PASSPHRASE = "rHGMPtr6oWw7VSa3W3wpa8fT8U"

ENCRYPTION_MARKER_V1 = "%"
ENCRYPTION_MARKER_V2 = "%="
COMPRESSION_MARKER = "~"


class EncryptionNotSupportedError(NotImplementedError):
    """Raised when encryption is requested.

    The MVP does not implement V1 or V2 encryption — see DESIGN.md
    "Known gaps". To use this server, disable End-to-End Encryption in
    the LiveSync plugin settings.
    """


def encrypt(plaintext: str, passphrase: str) -> str:
    """Encrypt with V2 HKDF and return the wire-format ciphertext.

    Not implemented in the MVP. See DESIGN.md "Known gaps".
    """
    raise EncryptionNotSupportedError(
        "encryption is not implemented in the MVP; disable E2EE in LiveSync settings"
    )


def decrypt(ciphertext: str, passphrase: str) -> str:
    """Auto-detect V1 / V2 from the marker and decrypt.

    Not implemented in the MVP. See DESIGN.md "Known gaps".
    """
    raise EncryptionNotSupportedError(
        "encryption is not implemented in the MVP; disable E2EE in LiveSync settings"
    )


def compress(data: str) -> str:
    """Deflate-compress and prepend the `~` marker.

    Output wire format: `~` + base64(raw_deflate(utf8(data))).
    Uses raw deflate (no zlib header) — matches `fflate.deflate()` output
    used by the plugin. See DESIGN.md "Known gaps" — there is also a
    newer `\\u{000E}LZ\\u{001D}` marker in the TS source that may
    supersede `~` for current LiveSync versions; needs verification.
    """
    raw = zlib.compress(data.encode("utf-8"), level=8)
    # zlib.compress produces a zlib-wrapped stream (header + adler32).
    # Strip the 2-byte zlib header and 4-byte adler32 trailer to get
    # raw deflate, matching fflate.deflate().
    deflate_payload = raw[2:-4]
    return COMPRESSION_MARKER + base64.b64encode(deflate_payload).decode("ascii")


def decompress(data: str) -> str:
    """Inverse of compress. Pass-through if no `~` marker."""
    if not is_compressed(data):
        return data
    payload = base64.b64decode(data[len(COMPRESSION_MARKER) :])
    # Raw deflate (no zlib header) — wbits=-15 tells zlib to skip header.
    decompressor = zlib.decompressobj(wbits=-15)
    raw = decompressor.decompress(payload) + decompressor.flush()
    return raw.decode("utf-8")


def is_encrypted(payload: str) -> bool:
    return payload.startswith(ENCRYPTION_MARKER_V1) or payload.startswith(ENCRYPTION_MARKER_V2)


def is_compressed(payload: str) -> bool:
    return payload.startswith(COMPRESSION_MARKER)
