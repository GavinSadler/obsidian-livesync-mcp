# Soft-Deleted Note

This note exists to be **deleted** after the first sync, so the exported
vault contains a LiveSync tombstone (`deleted: true`, children preserved).

## Why this note exists

The integration tests cover soft-delete behaviour:

1. A read of a soft-deleted note returns `None` (treated as not-found).
2. A soft-deleted note is excluded from `list_notes`.

Those tests **skip** when the export contains no deleted note, so the vault
needs one genuine tombstone to exercise them.

## How to delete it (see fixtures/source/README.md)

After the vault has synced once, delete this file in Obsidian and let
LiveSync sync again. The plugin writes a tombstone rather than purging
immediately, so a fresh export will include it.
