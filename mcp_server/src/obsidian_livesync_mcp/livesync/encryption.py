"""Encryption and compression for chunk payloads.

LiveSync supports several marker-prefixed encoding formats:

  - ``%=`` — HKDF over PBKDF2 (current default for E2EE-enabled vaults).
    Implemented here. Spec mirrored from
    octagonal-wheels/src/encryption/hkdf.ts.
  - ``%$`` — HKDF with an ephemeral PBKDF2 salt prepended to each
    ciphertext. Used for session-scoped encryption (e.g. journal-sync
    headers). Read support implemented; we don't generate these.
  - ``%`` — legacy PBKDF2 (pre-HKDF). Not implemented; raises.
  - ``%~`` — V3 (SHA-256-only KDF). Not implemented; raises.
  - ``~``  — raw deflate (zlib without header/trailer), applied before
    encryption when both are in use.

Wire format for ``%=``::

    "%=" + base64(iv[12] || hkdf_salt[32] || aes_gcm_ciphertext_and_tag)

Key derivation::

    master_key = PBKDF2-HMAC-SHA256(passphrase_utf8, pbkdf2_salt, 310_000 iter, 32 bytes)
    chunk_key  = HKDF-SHA256(master_key, salt=hkdf_salt, info=b"", 32 bytes)
    ciphertext = AES-GCM-256.encrypt(chunk_key, iv, plaintext_utf8)

The PBKDF2 salt is vault-wide and stored in CouchDB at the local doc
``_local/obsidian_livesync_sync_parameters`` (field ``pbkdf2salt``,
base64-encoded). Callers must fetch and pass it in.
"""

from __future__ import annotations

import base64
import os
import zlib
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 310_000
PBKDF2_SALT_LENGTH = 32
HKDF_SALT_LENGTH = 32
IV_LENGTH = 12
GCM_TAG_LENGTH = 16  # bytes; AES-GCM tag is appended to ciphertext by AESGCM
KEY_LENGTH = 32  # AES-256

HKDF_PREFIX = "%="
EPHEMERAL_HKDF_PREFIX = "%$"
LEGACY_V2_PREFIX = "%"  # bare "%" — must be checked AFTER %= and %$
V3_PREFIX = "%~"
COMPRESSION_MARKER = "~"

# Kept for backwards compatibility with existing imports.
ENCRYPTION_MARKER_V1 = LEGACY_V2_PREFIX
ENCRYPTION_MARKER_V2 = HKDF_PREFIX


class EncryptionNotSupportedError(NotImplementedError):
    """Raised for encryption formats this server doesn't implement.

    Currently raised for the legacy ``%`` (PBKDF2-only) and ``%~`` (V3)
    formats. ``%=`` (HKDF) and ``%$`` (HKDF with ephemeral salt) are
    supported.
    """


@lru_cache(maxsize=8)
def derive_master_key(passphrase: str, pbkdf2_salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256(310 000 iter), 32-byte output.

    Matches octagonal-wheels' ``deriveMasterKey``: feeds raw UTF-8
    passphrase bytes into PBKDF2 (no pre-hash).

    Cached: the master key depends only on (passphrase, vault salt), so we
    derive it once per vault instead of paying 310 000 iterations per chunk.
    """
    if len(pbkdf2_salt) != PBKDF2_SALT_LENGTH:
        raise ValueError(f"pbkdf2_salt must be {PBKDF2_SALT_LENGTH} bytes, got {len(pbkdf2_salt)}")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=pbkdf2_salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def derive_chunk_key(master_key: bytes, hkdf_salt: bytes) -> bytes:
    """HKDF-SHA256(master_key, salt=hkdf_salt, info=b""), 32-byte output."""
    if len(hkdf_salt) != HKDF_SALT_LENGTH:
        raise ValueError(f"hkdf_salt must be {HKDF_SALT_LENGTH} bytes, got {len(hkdf_salt)}")
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=hkdf_salt,
        info=b"",
    )
    return kdf.derive(master_key)


def encrypt_hkdf(plaintext: str, passphrase: str, pbkdf2_salt: bytes) -> str:
    """Encrypt with the ``%=`` HKDF format. Returns a base64 string."""
    iv = os.urandom(IV_LENGTH)
    hkdf_salt = os.urandom(HKDF_SALT_LENGTH)
    master = derive_master_key(passphrase, pbkdf2_salt)
    chunk_key = derive_chunk_key(master, hkdf_salt)
    aead = AESGCM(chunk_key).encrypt(iv, plaintext.encode("utf-8"), None)
    blob = iv + hkdf_salt + aead
    return HKDF_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_hkdf(
    ciphertext: str,
    passphrase: str,
    pbkdf2_salt: bytes,
    *,
    master_key: bytes | None = None,
) -> str:
    """Decrypt the ``%=`` HKDF format.

    Pass a pre-derived ``master_key`` to skip the PBKDF2 step when decrypting
    many chunks for the same vault in a tight loop.
    """
    if not ciphertext.startswith(HKDF_PREFIX):
        raise ValueError(f"expected ciphertext to start with {HKDF_PREFIX!r}")
    blob = base64.b64decode(ciphertext[len(HKDF_PREFIX) :])
    if len(blob) < IV_LENGTH + HKDF_SALT_LENGTH + GCM_TAG_LENGTH:
        raise ValueError("ciphertext too short to contain iv+salt+tag")
    iv = blob[:IV_LENGTH]
    hkdf_salt = blob[IV_LENGTH : IV_LENGTH + HKDF_SALT_LENGTH]
    aead = blob[IV_LENGTH + HKDF_SALT_LENGTH :]
    master = master_key if master_key is not None else derive_master_key(passphrase, pbkdf2_salt)
    chunk_key = derive_chunk_key(master, hkdf_salt)
    plaintext = AESGCM(chunk_key).decrypt(iv, aead, None)
    return plaintext.decode("utf-8")


def decrypt_ephemeral_hkdf(ciphertext: str, passphrase: str) -> str:
    """Decrypt the ``%$`` ephemeral-salt HKDF format.

    Wire format: ``"%$" + base64(pbkdf2_salt[32] || iv[12] || hkdf_salt[32] || aead)``.
    The PBKDF2 salt travels with the ciphertext instead of being shared
    vault-wide.
    """
    if not ciphertext.startswith(EPHEMERAL_HKDF_PREFIX):
        raise ValueError(f"expected ciphertext to start with {EPHEMERAL_HKDF_PREFIX!r}")
    blob = base64.b64decode(ciphertext[len(EPHEMERAL_HKDF_PREFIX) :])
    min_len = PBKDF2_SALT_LENGTH + IV_LENGTH + HKDF_SALT_LENGTH + GCM_TAG_LENGTH
    if len(blob) < min_len:
        raise ValueError("ciphertext too short for ephemeral-salt format")
    pbkdf2_salt = blob[:PBKDF2_SALT_LENGTH]
    rest = blob[PBKDF2_SALT_LENGTH:]
    iv = rest[:IV_LENGTH]
    hkdf_salt = rest[IV_LENGTH : IV_LENGTH + HKDF_SALT_LENGTH]
    aead = rest[IV_LENGTH + HKDF_SALT_LENGTH :]
    master = derive_master_key(passphrase, pbkdf2_salt)
    chunk_key = derive_chunk_key(master, hkdf_salt)
    return AESGCM(chunk_key).decrypt(iv, aead, None).decode("utf-8")


def decrypt(ciphertext: str, passphrase: str, pbkdf2_salt: bytes | None = None) -> str:
    """Marker-dispatched decrypt for any supported format.

    Raises :class:`EncryptionNotSupportedError` for ``%`` (legacy V2 PBKDF2)
    and ``%~`` (V3). Requires ``pbkdf2_salt`` for ``%=`` payloads.
    """
    if ciphertext.startswith(HKDF_PREFIX):
        if pbkdf2_salt is None:
            raise ValueError("pbkdf2_salt is required to decrypt %= payloads")
        return decrypt_hkdf(ciphertext, passphrase, pbkdf2_salt)
    if ciphertext.startswith(EPHEMERAL_HKDF_PREFIX):
        return decrypt_ephemeral_hkdf(ciphertext, passphrase)
    if ciphertext.startswith(V3_PREFIX):
        raise EncryptionNotSupportedError(
            "V3 (%~) encryption is not supported; only HKDF (%=) is implemented"
        )
    if ciphertext.startswith(LEGACY_V2_PREFIX):
        raise EncryptionNotSupportedError(
            "legacy V2 (%) PBKDF2 encryption is not supported; only HKDF (%=) is implemented"
        )
    raise ValueError("payload has no recognized encryption marker")


def encrypt(plaintext: str, passphrase: str, pbkdf2_salt: bytes) -> str:
    """Encrypt with the default supported format (HKDF, ``%=``)."""
    return encrypt_hkdf(plaintext, passphrase, pbkdf2_salt)


def compress(data: str) -> str:
    """Deflate-compress and prepend the ``~`` marker.

    Output wire format: ``~`` + base64(raw_deflate(utf8(data))). Uses raw
    deflate (no zlib header) — matches ``fflate.deflate()`` output used
    by the plugin.
    """
    raw = zlib.compress(data.encode("utf-8"), level=8)
    # zlib.compress wraps the deflate stream with a 2-byte header and a
    # 4-byte adler32 trailer. Strip both to get raw deflate.
    deflate_payload = raw[2:-4]
    return COMPRESSION_MARKER + base64.b64encode(deflate_payload).decode("ascii")


def decompress(data: str) -> str:
    """Inverse of :func:`compress`. Pass-through if no ``~`` marker."""
    if not is_compressed(data):
        return data
    payload = base64.b64decode(data[len(COMPRESSION_MARKER) :])
    decompressor = zlib.decompressobj(wbits=-15)
    raw = decompressor.decompress(payload) + decompressor.flush()
    return raw.decode("utf-8")


def is_encrypted(payload: str) -> bool:
    """True for any supported or unsupported encryption marker."""
    return (
        payload.startswith(HKDF_PREFIX)
        or payload.startswith(EPHEMERAL_HKDF_PREFIX)
        or payload.startswith(V3_PREFIX)
        or payload.startswith(LEGACY_V2_PREFIX)
    )


def is_hkdf_encrypted(payload: str) -> bool:
    return payload.startswith(HKDF_PREFIX) or payload.startswith(EPHEMERAL_HKDF_PREFIX)


def is_compressed(payload: str) -> bool:
    return payload.startswith(COMPRESSION_MARKER)
