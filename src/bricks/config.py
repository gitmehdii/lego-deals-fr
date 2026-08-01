from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic import SecretStr as SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.util import asbool

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# The form a remote libSQL URL has to take, quoted in every error below so the
# fix is in the message rather than in a document the reader has to go find.
LIBSQL_URL_SHAPE = "sqlite+libsql://<db>.turso.io/?authToken=<token>&secure=true"

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

    @field_validator("*", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object, info: ValidationInfo) -> object:
        """A variable set to nothing is a variable nobody set.

        Two places produce a blank rather than an absent value, and both are
        the documented first step: `.env.example` ships its optional keys empty
        for you to fill in, and GitHub Actions injects "" for every secret that
        does not exist. Without this, `cp .env.example .env` fails on
        DISCORD_WEBHOOK_URL, and a workflow missing DEALABS_RSS_URL overrides
        the feed with an empty string instead of falling back to the default.
        """
        if not isinstance(value, str) or value.strip():
            return value
        field = cls.model_fields.get(info.field_name) if info.field_name else None
        return field.default if field is not None else None

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("database_url", mode="after")
    @classmethod
    def _turso_url_must_be_secure(cls, value: str) -> str:
        """Refuse a Turso URL that would carry its auth token in the clear.

        sqlalchemy-libsql picks http:// or https:// from a `secure` flag in the
        query string and **defaults it to false**, so the obvious URL — host
        plus authToken — connects over plain http with the token sitting in
        that same query string. Nothing downstream notices: the run either
        works or fails for a reason that says nothing about why.

        Never echoes the value, which is itself the credential.
        """
        try:
            url = make_url(value)
        except ArgumentError:
            raise ValueError(
                "DATABASE_URL is not a valid SQLAlchemy URL. Expected "
                f"sqlite:///local.db locally, or {LIBSQL_URL_SHAPE}"
            ) from None

        # What Turso's dashboard hands out. SQLAlchemy has no dialect under
        # that name and fails at create_engine() with NoSuchModuleError, far
        # from the paste that caused it.
        if url.drivername == "libsql":
            raise ValueError(
                "libsql:// is Turso's own scheme, not a SQLAlchemy one. "
                f"Prefix it with sqlite+ : {LIBSQL_URL_SHAPE}"
            )

        # No host means a local file opened through the libSQL driver, which
        # never leaves the machine and needs no scheme at all.
        if "libsql" not in url.drivername or not url.host:
            return value

        if not _is_true(url.query.get("secure")):
            raise ValueError(
                "a remote libSQL URL must carry secure=true, otherwise the "
                "driver connects over plain http and the authToken travels in "
                f"clear text. Expected {LIBSQL_URL_SHAPE}"
            )
        return value

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


def _is_true(flag: object) -> bool:
    """Read `secure` exactly as the dialect will.

    asbool is what sqlalchemy-libsql itself coerces the flag with, so the two
    cannot drift on which spellings count as true. A value it refuses is not
    secure either; the caller reports the shape it wants instead.
    """
    if isinstance(flag, tuple):
        flag = flag[-1] if flag else None
    if flag is None:
        return False
    try:
        return bool(asbool(flag))
    except ValueError:
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
