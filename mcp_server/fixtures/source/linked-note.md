# Linked Note

This note is a target of wikilinks from [[note-with-links]].

## Purpose

This note demonstrates:
1. Being referenced by other notes
2. Backfill of `link_refs` (reverse link tracking)
3. That the link graph is bidirectional

When [[note-with-links]] is processed, this note should get a `link_refs` entry showing that it's linked from `note-with-links.md`.

## Link Back

- [[note-with-links]] - linked back for testing bidirectional links

The test suite verifies:
- Forward links in `note-with-links` point to this note
- Reverse links in this note are populated by backfill
- Both directions work correctly
