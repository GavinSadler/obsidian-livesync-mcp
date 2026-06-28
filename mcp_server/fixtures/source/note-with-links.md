# Note with Wikilinks

This note tests wikilink extraction and the link backfill logic.

## Outgoing Links

- [[linked-note]] - this note links to another note
- [[README]] - link back to the index
- [[compressible-note]] - test cross-linking

## Link Format Variations

- `[[absolute-path]]` - basic format
- `[[../sibling-note]]` - relative path (if it exists)
- `[[note|display text]]` - aliased link
- `[[linked-note|See this note]]` - aliased link example

## Why This Matters

The NoteRepository's backfill logic needs to:
1. Extract all wikilinks from the note text
2. Resolve paths to document IDs
3. Store reverse links in `link_refs` 
4. Handle both `[[path]]` and `[[path|alias]]` formats

This note provides multiple wikilinks so we can verify:
- Forward links are extracted correctly
- Linked notes get `link_refs` entries pointing back
- Aliased links are handled properly
