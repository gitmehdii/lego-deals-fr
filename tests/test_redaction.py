import json

import pytest

from bricks.log import configure_logging, get_logger, redact_secrets

TURSO_URL = "sqlite+libsql://bricks-db.turso.io?authToken=eyJhbGciOiJFZERTQSJ9"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "could not connect to sqlite+libsql://db.turso.io?authToken=abc123",
            "could not connect to sqlite+libsql://db.turso.io?authToken=***",
        ),
        (
            "https://api.brickset.com/v3/getSets?apiKey=abc123&setNumber=10497-1",
            "https://api.brickset.com/v3/getSets?apiKey=***&setNumber=10497-1",
        ),
        (
            "https://host/path?a=1&token=abc123&b=2",
            "https://host/path?a=1&token=***&b=2",
        ),
        ("https://host/path?AUTHTOKEN=abc123", "https://host/path?AUTHTOKEN=***"),
        ("https://host/path?access_token=abc123", "https://host/path?access_token=***"),
        (
            "https://host/path?client_secret=abc123",
            "https://host/path?client_secret=***",
        ),
        ("https://host/path?password=abc123", "https://host/path?password=***"),
        ("https://host/path;token=abc123", "https://host/path;token=***"),
    ],
)
def test_credential_parameters_are_redacted(text, expected):
    assert redact_secrets(text) == expected


def test_several_parameters_are_redacted_in_one_string():
    redacted = redact_secrets("https://host?token=aaa&keep=yes&apiKey=bbb")
    assert "aaa" not in redacted
    assert "bbb" not in redacted
    assert "keep=yes" in redacted


def test_harmless_parameters_are_left_alone():
    url = "https://dealabs.test/rss?q=lego&page=2"
    assert redact_secrets(url) == url


def test_text_without_a_url_is_unchanged():
    message = "no such table: offers"
    assert redact_secrets(message) == message


def test_empty_value_is_still_redacted():
    assert redact_secrets("https://host?token=") == "https://host?token=***"


def test_redaction_stops_at_whitespace():
    redacted = redact_secrets(f"connect to {TURSO_URL} failed after 3 tries")
    assert "eyJhbGciOiJFZERTQSJ9" not in redacted
    assert redacted.endswith("failed after 3 tries")


def test_logged_event_values_are_redacted(capsys):
    configure_logging("INFO")
    get_logger("test").error("run_failed", error=f"cannot reach {TURSO_URL}")

    record = json.loads(capsys.readouterr().out)
    assert "eyJhbGciOiJFZERTQSJ9" not in record["error"]
    assert "authToken=***" in record["error"]


def test_logged_traceback_is_redacted(capsys):
    configure_logging("INFO")
    try:
        raise RuntimeError(f"connection to {TURSO_URL} refused")
    except RuntimeError:
        get_logger("test").exception("run_failed")

    output = capsys.readouterr().out
    assert "eyJhbGciOiJFZERTQSJ9" not in output
    assert "authToken=***" in output
