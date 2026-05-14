"""Runtime configuration loaded from env vars / .env."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CouchDBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COUCHDB_", env_file=".env", extra="ignore")

    url: str = Field(..., description="Base URL of the CouchDB server, e.g. https://host:6984")
    database: str = Field(..., description="Database name holding the LiveSync vault")
    username: str | None = None
    password: SecretStr | None = None
    verify_tls: bool = True


class LiveSyncSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIVESYNC_", env_file=".env", extra="ignore")

    passphrase: SecretStr | None = Field(
        default=None,
        description="E2EE passphrase. Required if the vault is encrypted.",
    )
    obfuscate_paths: bool = Field(
        default=False,
        description="Whether the vault uses path obfuscation (f: prefix).",
    )


class VectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VECTOR_", env_file=".env", extra="ignore")

    enabled: bool = True
    store: str = Field(default="sqlite-vec", description="Vector store backend identifier")
    store_path: str = Field(default="./vector.db", description="Local path for embedded stores")
    embedding_backend: str = Field(default="openai")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimension: int = 1536


class Settings(BaseSettings):
    """Top-level settings aggregating all subsystems."""

    couchdb: CouchDBSettings
    livesync: LiveSyncSettings
    vector: VectorSettings


def load_settings() -> Settings:
    """Load settings from environment / .env file."""
    raise NotImplementedError
