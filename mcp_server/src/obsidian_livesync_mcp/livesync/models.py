"""Pydantic models mirroring the LiveSync CouchDB document schema.

Authoritative source: src/lib/src/common/models/db.type.ts in the
livesync-commonlib submodule.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

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
