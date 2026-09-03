"""Application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_destination_country: str = "TR"
    default_reference_currency: str = "TRY"
    fx_provider: str = "frankfurter"
    offer_cache_ttl_seconds: int = 900
    ebay_app_id: str | None = None
    ebay_dev_id: str | None = None
    ebay_cert_id: str | None = None
    ebay_sandbox: bool = False
    data_dir: Path = DATA_DIR


@lru_cache
def get_settings() -> Settings:
    return Settings()
