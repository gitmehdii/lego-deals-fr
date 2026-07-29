import pytest
from pydantic import ValidationError
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy_libsql.libsql import SQLiteDialect_libsql

from bricks.config import Settings, get_settings


def settings(**overrides) -> Settings:
    """_env_file=None: never read a real .env, whatever the machine."""
    return Settings(_env_file=None, **overrides)


def test_defaults_match_the_documented_ones(monkeypatch):
    # The isolated_database fixture points DATABASE_URL at a temp file so no
    # test can touch the developer's local.db. This is the one test that wants
    # the bare default, so it clears the variable first.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = settings()
    assert config.database_url == "sqlite:///local.db"
    assert config.min_discount_pct == 25.0
    assert config.min_resolution_score == 0.85
    assert config.log_level == "INFO"


def test_secrets_are_absent_by_default():
    config = settings()
    assert config.brickset_api_key is None
    assert config.discord_webhook_url is None


def test_secrets_do_not_leak_when_rendered(monkeypatch):
    monkeypatch.setenv(
        "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/s3cret"
    )
    config = settings()
    assert "s3cret" not in repr(config)
    assert "s3cret" not in str(config.discord_webhook_url)
    assert config.discord_webhook_url.get_secret_value().endswith("s3cret")


def test_reads_environment_variables(monkeypatch):
    monkeypatch.setenv("MIN_DISCOUNT_PCT", "40")
    monkeypatch.setenv("DEALABS_RSS_URL", "https://dealabs.test/rss")
    config = settings()
    assert config.min_discount_pct == 40.0
    assert config.dealabs_rss_url == "https://dealabs.test/rss"


def test_log_level_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert settings().log_level == "DEBUG"


def test_unknown_log_level_is_rejected(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize("value", ["-1", "101"])
def test_discount_is_a_percentage_between_0_and_100(monkeypatch, value):
    monkeypatch.setenv("MIN_DISCOUNT_PCT", value)
    with pytest.raises(ValidationError):
        settings()


@pytest.mark.parametrize("value", ["-0.1", "1.1"])
def test_resolution_score_is_a_ratio_between_0_and_1(monkeypatch, value):
    monkeypatch.setenv("MIN_RESOLUTION_SCORE", value)
    with pytest.raises(ValidationError):
        settings()


def test_a_channel_link_is_rejected_as_a_webhook_url(monkeypatch):
    """The "Copy Link" button gives this, and it is not an endpoint."""
    monkeypatch.setenv(
        "DISCORD_WEBHOOK_URL", "https://discord.com/channels/123456789/987654321"
    )
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="must be a webhook URL"):
        Settings()


def test_the_rejection_never_echoes_the_url(monkeypatch):
    """A pydantic error repr must not become the thing that leaks it."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://evil.test/s3cret-token")
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    assert "s3cret-token" not in str(excinfo.value)


@pytest.mark.parametrize(
    "host", ["discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"]
)
def test_every_discord_host_is_accepted(monkeypatch, host):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", f"https://{host}/api/webhooks/1/tok")
    get_settings.cache_clear()
    assert Settings().discord_webhook_url is not None


def test_no_webhook_configured_is_still_valid(monkeypatch):
    """Running without Discord is a supported mode, not an error."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    assert Settings().discord_webhook_url is None


SECURE_TURSO_URL = "sqlite+libsql://db.turso.test/?authToken=x&secure=true"

# Every spelling that must be refused, and why it is dangerous or useless.
REJECTED_DATABASE_URLS = [
    # The obvious URL, and the one Turso's own docs lead you to write. The
    # driver defaults `secure` to false, so this connects over plain http with
    # the token in the query string.
    "sqlite+libsql://db.turso.test/?authToken=x",
    "sqlite+libsql://db.turso.test?authToken=x",
    "sqlite+libsql://db.turso.test/?authToken=x&secure=false",
    # A flag the driver itself cannot read is not a flag that made it secure.
    "sqlite+libsql://db.turso.test/?authToken=x&secure=oui",
    # Turso's own scheme, straight from `turso db show --url`. No SQLAlchemy
    # dialect answers to it.
    "libsql://db.turso.test/?authToken=x&secure=true",
    "not a url at all",
]

ACCEPTED_DATABASE_URLS = [
    "sqlite:///local.db",
    # A local file opened through the libSQL driver never leaves the machine,
    # so it needs neither a scheme nor a token.
    "sqlite+libsql:///local.db",
    SECURE_TURSO_URL,
    "sqlite+libsql://db.turso.test/?authToken=x&secure=1",
    "sqlite+libsql://db.turso.test/?authToken=x&secure=yes",
]


def test_a_turso_url_can_actually_be_opened():
    """CLAUDE.md and .env.example both advertise sqlite+libsql:// for
    production, and the GitHub runner's disk is wiped between runs, so an
    on-disk SQLite would start empty every time. Without the dialect installed
    this raises NoSuchModuleError before any query is attempted."""
    from sqlalchemy import create_engine

    engine = create_engine(SECURE_TURSO_URL)
    assert engine.dialect.driver == "libsql"


@pytest.mark.parametrize("url", REJECTED_DATABASE_URLS)
def test_an_unusable_database_url_is_rejected(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("url", ACCEPTED_DATABASE_URLS)
def test_a_usable_database_url_is_accepted(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    assert Settings().database_url == url


def test_rejecting_a_database_url_never_echoes_its_token(monkeypatch):
    """The whole point of the check is the token in that query string."""
    monkeypatch.setenv(
        "DATABASE_URL", "sqlite+libsql://db.turso.test/?authToken=s3cret"
    )
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    assert "s3cret" not in str(excinfo.value)


@pytest.mark.parametrize("url", [*REJECTED_DATABASE_URLS, *ACCEPTED_DATABASE_URLS])
def test_the_secure_rule_is_pinned_to_the_driver(monkeypatch, url):
    """Pin the rule to sqlalchemy-libsql rather than to a comment.

    `secure` is the driver's flag, not ours, and the validator only restates
    what it means. This runs both lists through the real dialect and asserts
    the two agree: nothing we accept builds a plain-http connection, and
    everything we reject over that flag genuinely would have. If the driver
    ever flips its default, this fails instead of quietly over-refusing.
    """
    accepted = _accepts(monkeypatch, url)

    try:
        parsed = make_url(url)
    except ArgumentError:
        assert not accepted, f"{url} cannot even be parsed, it must be refused"
        return
    if parsed.drivername != "sqlite+libsql":
        return

    built = _connect_url(parsed)
    if built is None:
        # The driver cannot build a connection from it at all. Refusing at
        # startup with our own message beats failing later on the driver's
        # "String is not true/false".
        assert not accepted, f"{url} is unusable but was accepted"
    elif accepted:
        assert not built.startswith("http://"), f"{url} would send the token in clear"
    else:
        assert built.startswith("http://"), (
            f"{url} was refused, but the driver would not have used plain http"
        )


def _accepts(monkeypatch, url: str) -> bool:
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        Settings()
    except ValidationError:
        return False
    return True


def _connect_url(parsed: URL) -> str | None:
    """What the real dialect would dial, or None if it refuses the URL."""
    try:
        args, _ = SQLiteDialect_libsql().create_connect_args(parsed)
    except ValueError:
        return None
    return args[0]
