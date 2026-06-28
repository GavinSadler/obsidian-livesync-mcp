---
title: Fixture Vault Documentation
tags: [fixture, docs]
created: 2025-01-01
---

# Fixture Vault Documentation

This is a test fixture vault for obsidian-livesync integration tests.

## What's Tested

- **Frontmatter extraction** (this note has YAML front matter)
- **Basic wikilinks** (see [[note-with-links]])
- **Path normalization** (file lives at `README.md`)

## Wikilink Examples

- Link to [[note-with-links]] (should backfill)
- Link to [[linked-note]] (should backfill)
- Link to [[compressible-note]] (should backfill)

This vault demonstrates:
1. Frontmatter parsing
2. Wikilink extraction
3. Basic CRUD operations
