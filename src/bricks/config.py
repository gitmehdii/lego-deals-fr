from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic import SecretStr as SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///local.db"

    brickset_api_key: SecretStr | None = None
    discord_webhook_url: SecretStr | None = None
    # Dealabs' own LEGO group feed. A personal keyword-alert feed substitutes
    # for it without a code change; that URL is personal, treat it as a secret.
    dealabs_rss_url: str = "https://www.dealabs.com/rss/groupe/lego"

    # Catalogue endpoints. Not secrets, but no external URL is hardcoded here:
    # the day a provider moves a file, it is a config change, not a release.
    rebrickable_sets_url: str = (
        "https://cdn.rebrickable.com/media/downloads/sets.csv.gz"
    )
    rebrickable_themes_url: str = (
        "https://cdn.rebrickable.com/media/downloads/themes.csv.gz"
    )
    brickset_api_url: str = "https://brickset.com/api/v3.asmx"

    # Percentage, 0-100. Same unit as alerts.discount_pct in the database.
    min_discount_pct: float = Field(default=25.0, ge=0.0, le=100.0)

    # Confidence ratio, 0-1. Same unit as offers.resolution_score.
    min_resolution_score: float = Field(default=0.85, ge=0.0, le=1.0)

    log_level: LogLevel = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
