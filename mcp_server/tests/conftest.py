"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_passphrase() -> str:
    return "correct horse battery staple"


@pytest.fixture
def sample_note_content() -> str:
    return (
        "# Hello\n\n"
        "This is a test note.\n\n"
        "## Section A\n\n"
        "Some content under A.\n\n"
        "## Section B\n\n"
        "Some content under B.\n"
    )
