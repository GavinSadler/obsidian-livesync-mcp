"""Pydantic models mirroring the LiveSync CouchDB document schema.

Authoritative source: src/lib/src/common/models/db.type.ts in the
livesync-commonlib submodule.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import yaml
from pydantic import BaseModel, Field


class DocType(StrEnum):
    PLAIN = "plain"  # text file
    NEWNOTE = "newnote"  # binary file
    LEAF = "leaf"  # content chunk
    CHUNKPACK = "chunkpack"
    VERSIONINFO = "versioninfo"
    SYNCINFO = "syncinfo"
    MILESTONEINFO = "milestoneinfo"
    NODEINFO = "nodeinfo"


class NoteDocument(BaseModel):
    """A LiveSync note metadata document (the 'parent' doc for chunks)."""

    id: str = Field(alias="_id")
    rev: str | None = Field(default=None, alias="_rev")
    deleted_field: bool | None = Field(default=None, alias="_deleted")

    type: DocType
    path: str
    ctime: int
    mtime: int
    size: int
    children: list[str] = Field(default_factory=list)
    eden: dict[str, Any] = Field(default_factory=dict)
    deleted: bool | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class ChunkDocument(BaseModel):
    """A content chunk referenced by a NoteDocument.children."""

    id: str = Field(alias="_id")
    rev: str | None = Field(default=None, alias="_rev")

    type: DocType = DocType.LEAF
    data: str
    e_: bool | None = None  # encrypted flag

    model_config = {"populate_by_name": True, "extra": "allow"}


def extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter from note content.

    Assumes YAML frontmatter at the top between `---` markers (Jekyll/Hugo
    standard). Returns (parsed_dict, remaining_content).

    If no frontmatter or malformed YAML, returns ({}, original_content).
    """
    if not content.startswith("---"):
        return {}, content

    end_marker = content.find("\n---\n", 3)
    if end_marker == -1:
        return {}, content

    yaml_text = content[3:end_marker]
    remaining = content[end_marker + 5 :]

    try:
        frontmatter = yaml.safe_load(yaml_text) or {}
        if not isinstance(frontmatter, dict):
            return {}, content
        return frontmatter, remaining
    except yaml.YAMLError:
        return {}, content


def extract_tags(frontmatter: dict[str, Any]) -> list[str]:
    """Extract tags from parsed frontmatter dict.

    Looks for `tags` or `keywords` fields; accepts strings, lists, or
    comma-separated strings. Returns a flat list of lowercase tags.
    """
    tags: list[str] = []

    for field in ("tags", "keywords"):
        if field not in frontmatter:
            continue

        value = frontmatter[field]
        if isinstance(value, str):
            tags.extend(t.strip() for t in value.split(",") if t.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    tags.extend(t.strip() for t in item.split(",") if t.strip())

    return [t.lower() for t in tags]
