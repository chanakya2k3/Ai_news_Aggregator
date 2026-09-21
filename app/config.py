"""Application settings, loaded once from the .env file in the project root.

Nothing else in the app reads environment variables directly; everything imports
`settings` from here, so there is a single place to look for configuration.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore variables we don't use yet
    )

    # PostgreSQL
    postgres_user: str = "ai_news"
    postgres_password: str = "change-me-locally"
    postgres_db: str = "ai_news"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    @property
    def email_configured(self) -> bool:
        """True once .env has enough filled in to actually send mail."""
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.email_to)

    @property
    def database_url(self) -> str:
        """SQLAlchemy connection string, e.g. postgresql+psycopg://user:pass@localhost:5432/ai_news"""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is read only once per process."""
    return Settings()


settings = get_settings()
