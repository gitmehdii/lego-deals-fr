from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic import SecretStr as SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# What Discord's "Copy Webhook URL" button produces. The far more available
# "Copy Link" button on a channel gives https://discord.com/channels/... — a
# web page, not an endpoint — and pasting that instead is an easy mistake to
# make and a confusing one to debug once a run is already underway.
_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Pydantic quotes the offending value in its error message by default,
        # which for a settings class made mostly of credentials means a
        # malformed key or webhook URL gets printed the moment it is wrong.
        # SecretStr does not help: the error records the raw input.
        hide_input_in_errors=True,
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

    @field_validator("discord_webhook_url", mode="after")
    @classmethod
    def _must_be_a_webhook_url(cls, value: SecretStr | None) -> SecretStr | None:
        """Fail at startup rather than mid-run, and never echo the value.

        Validated after coercion to SecretStr so that pydantic prints
        `**********` rather than the URL if it reports the error.
        """
        if value is None:
            return None
        if not value.get_secret_value().startswith(_WEBHOOK_PREFIXES):
            raise ValueError(
                "DISCORD_WEBHOOK_URL must be a webhook URL "
                "(https://discord.com/api/webhooks/...), obtained from the "
                "channel's Integrations settings. A channel link copied from "
                "the app (https://discord.com/channels/...) is a web page, "
                "not an endpoint."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
