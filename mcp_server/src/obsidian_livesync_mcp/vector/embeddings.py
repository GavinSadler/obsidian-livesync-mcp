"""Embedding backend abstraction."""

from __future__ import annotations

from typing import Protocol


class EmbeddingBackend(Protocol):
    """Pluggable embedding provider."""

    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of strings; return one vector per input."""
        ...

    async def close(self) -> None: ...


def make_backend(name: str, model: str, dimension: int) -> EmbeddingBackend:
    """Factory: instantiate a backend from a config identifier."""
    raise NotImplementedError
