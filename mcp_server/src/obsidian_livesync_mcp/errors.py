"""Typed exceptions surfaced from the MCP tool layer.

Each maps to a structured MCP error so the LLM can react appropriately
(e.g., "note doesn't exist" → consider `create_note` instead).
"""

from __future__ import annotations


class LiveSyncMCPError(Exception):
    """Base for all errors raised by this server."""


class NoteNotFoundError(LiveSyncMCPError):
    """Path doesn't exist, or note is soft-deleted (treated the same)."""


class NoteAlreadyExistsError(LiveSyncMCPError):
    """A non-deleted note already exists at the target path."""


class InvalidPathError(LiveSyncMCPError):
    """Path is empty, has a leading slash, contains `..`, or otherwise malformed."""


class NoteWriteError(LiveSyncMCPError):
    """Internal: 409 retries exhausted, or other write-side failure."""


class MovePartialFailureError(LiveSyncMCPError):
    """move_note created the destination but failed to delete the source.

    The note now exists at both paths; the caller can clean up by
    calling `delete_note(old_path)` directly.
    """

    def __init__(self, old_path: str, new_path: str, reason: str) -> None:
        super().__init__(
            f"move partially succeeded: created '{new_path}' but failed to delete "
            f"'{old_path}': {reason}. Call delete_note('{old_path}') to clean up."
        )
        self.old_path = old_path
        self.new_path = new_path
        self.reason = reason


class CouchDBError(LiveSyncMCPError):
    """Unexpected error from the CouchDB HTTP API."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"CouchDB {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class EncryptedVaultError(LiveSyncMCPError):
    """The vault appears to be encrypted; the MVP doesn't support that."""
