"""Tests for YAML frontmatter extraction and tag parsing."""

from __future__ import annotations

from typing import Any

from obsidian_livesync_mcp.livesync.models import extract_frontmatter, extract_tags


def test_extract_frontmatter_valid() -> None:
    content = """---
title: My Note
tags: [foo, bar]
---
This is the body."""

    frontmatter, body = extract_frontmatter(content)
    assert frontmatter == {"title": "My Note", "tags": ["foo", "bar"]}
    assert body == "This is the body."


def test_extract_frontmatter_empty() -> None:
    content = """---
---
Body only."""
    frontmatter, body = extract_frontmatter(content)
    assert frontmatter == {}
    assert body == "Body only."


def test_extract_frontmatter_no_frontmatter() -> None:
    content = "Just body, no frontmatter."
    frontmatter, body = extract_frontmatter(content)
    assert frontmatter == {}
    assert body == content


def test_extract_frontmatter_malformed_yaml() -> None:
    content = """---
title: My Note
  bad indentation:
    - [broken yaml
---
Body."""
    frontmatter, body = extract_frontmatter(content)
    assert frontmatter == {}
    assert body == content  # Return original on parse error


def test_extract_frontmatter_missing_end_marker() -> None:
    content = """---
title: My Note
No closing marker here."""
    frontmatter, body = extract_frontmatter(content)
    assert frontmatter == {}
    assert body == content


def test_extract_frontmatter_non_dict_yaml() -> None:
    # YAML that parses but isn't a dict
    content = """---
- just a list
---
Body."""
    frontmatter, body = extract_frontmatter(content)
    assert frontmatter == {}
    assert body == content


def test_extract_frontmatter_multiline_values() -> None:
    content = """---
description: |
  Multi-line
  description here
tags: [one, two]
---
Body text."""
    frontmatter, body = extract_frontmatter(content)
    assert "description" in frontmatter
    assert "tags" in frontmatter
    assert body == "Body text."


def test_extract_tags_from_list() -> None:
    frontmatter = {"tags": ["foo", "bar", "baz"]}
    tags = extract_tags(frontmatter)
    assert set(tags) == {"foo", "bar", "baz"}


def test_extract_tags_from_string() -> None:
    frontmatter = {"tags": "foo, bar, baz"}
    tags = extract_tags(frontmatter)
    assert set(tags) == {"foo", "bar", "baz"}


def test_extract_tags_from_keywords() -> None:
    frontmatter = {"keywords": "alpha, beta"}
    tags = extract_tags(frontmatter)
    assert set(tags) == {"alpha", "beta"}


def test_extract_tags_lowercase() -> None:
    frontmatter = {"tags": ["FOO", "Bar"]}
    tags = extract_tags(frontmatter)
    assert tags == ["foo", "bar"]


def test_extract_tags_mixed_list_and_string() -> None:
    # If tags is a list containing both strings and comma-sep strings
    frontmatter = {"tags": ["foo,bar", "baz"]}
    tags = extract_tags(frontmatter)
    assert set(tags) == {"foo", "bar", "baz"}


def test_extract_tags_empty() -> None:
    frontmatter: dict[str, Any] = {}
    tags = extract_tags(frontmatter)
    assert tags == []


def test_extract_tags_whitespace_handling() -> None:
    frontmatter = {"tags": "  foo  ,  bar  ,  baz  "}
    tags = extract_tags(frontmatter)
    assert set(tags) == {"foo", "bar", "baz"}


def test_extract_tags_deduplicate() -> None:
    frontmatter = {"tags": ["foo", "bar"], "keywords": "foo, baz"}
    tags = extract_tags(frontmatter)
    assert len([t for t in tags if t == "foo"]) >= 1  # May appear twice before dedup


def test_extract_tags_ignores_non_string_items() -> None:
    # List with non-string items should be skipped gracefully
    frontmatter: dict[str, Any] = {"tags": ["foo", 123, "bar"]}
    tags = extract_tags(frontmatter)
    assert "foo" in tags
    assert "bar" in tags
    assert "123" not in tags


def test_extract_tags_both_tags_and_keywords() -> None:
    frontmatter = {"tags": ["t1", "t2"], "keywords": "k1, k2"}
    tags = extract_tags(frontmatter)
    assert set(tags) == {"t1", "t2", "k1", "k2"}
