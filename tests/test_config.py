import pytest
from pydantic import ValidationError

from bricks.config import Settings


def settings(**overrides) -> Settings:
    """_env_file=None: never read a real .env, whatever the machine."""
    return Settings(_env_file=None, **overrides)


def test_defaults_match_the_documented_ones():
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
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook/s3cret")
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
