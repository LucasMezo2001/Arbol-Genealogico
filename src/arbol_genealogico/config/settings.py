from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    config_path: Path = Path("config/config.yaml")
    database_url: str = "postgresql+asyncpg://arbol:arbol@localhost:5432/arbol"

    scraper_base_url: str = "https://internet.aheb-beha.org"
    scraper_user_agent: str = "arbol-genealogico/0.1 (uso genealogico personal)"
    scraper_min_delay_s: float = 0.8
    scraper_max_delay_s: float = 1.6
    scraper_max_retries: int = 5
    scraper_raw_dir: Path = Path("data/raw")
