"""Application settings, read from environment variables (and `.env` when present)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Every field has a matching line in `.env.example`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ShockGraph PEA"
    app_version: str = "0.1.0"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme-neo4j"
    neo4j_database: str = "neo4j"

    api_key: str = "changeme-api-key"

    llm_provider: str = "ollama"  # "ollama" or "fake" (tests and CI never call a real model)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model_large: str = "qwen2.5:7b"
    ollama_model_small: str = "qwen2.5:3b"

    fred_api_key: str = ""
    openfigi_api_key: str = ""
    sec_user_agent: str = "ShockGraph PEA student project contact@example.com"

    data_dir: str = "data"
    returns_window_years: int = 3
    min_r2_default: float = 0.1


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
