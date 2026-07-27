import json

import pytest

from bricks.log import (
    REDACTION_FAILED,
    configure_logging,
    get_logger,
    redact_secrets,
)

TURSO_URL = "sqlite+libsql://bricks-db.turso.io?authToken=eyJhbGciOiJFZERTQSJ9"
NEON_URL = (
    "postgresql://bricks_owner:npg_Xy7pQ2@ep-cool-1.eu-central-1.aws.neon.tech/bricks"
)


class Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("__str__ exploded")


class LeakyRepr:
    def __str__(self) -> str:
        return f"connection to {NEON_URL} refused"


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "postgresql://owner:npg_Xy7pQ2@ep-cool.aws.neon.tech/bricks",
            "postgresql://owner:***@ep-cool.aws.neon.tech/bricks",
        ),
        (
            "could not translate host name in postgresql://u:p@db:5432/x",
            "could not translate host name in postgresql://u:***@db:5432/x",
        ),
        ("https://user:@host/path", "https://user:***@host/path"),
    ],
)
def test_url_userinfo_passwords_are_redacted(text, expected):
    assert redact_secrets(text) == expected


def test_userinfo_redaction_keeps_the_username():
    redacted = redact_secrets(NEON_URL)
    assert "npg_Xy7pQ2" not in redacted
    assert "bricks_owner" in redacted


def test_a_bare_email_address_is_not_userinfo():
    message = "contact bricks@example.test for access"
    assert redact_secrets(message) == message


def test_url_without_userinfo_is_unchanged():
    for url in ("sqlite:///local.db", "https://host/path", "https://host@path"):
        assert redact_secrets(url) == url


def test_both_credential_shapes_in_one_string():
    redacted = redact_secrets(f"tried {NEON_URL} then {TURSO_URL}")
    assert "npg_Xy7pQ2" not in redacted
    assert "eyJhbGciOiJFZERTQSJ9" not in redacted


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "None"),
        (42, "42"),
        (3.14, "3.14"),
        (True, "True"),
        ("", ""),
        (b"bytes", "b'bytes'"),
        (["a", "b"], "['a', 'b']"),
        ({"k": "v"}, "{'k': 'v'}"),
    ],
)
def test_non_string_input_is_accepted(value, expected):
    assert redact_secrets(value) == expected


def test_object_whose_str_raises_yields_the_failure_marker():
    assert redact_secrets(Unprintable()) == REDACTION_FAILED


def test_failure_marker_never_carries_the_original_value():
    class LeakyAndBroken:
        def __str__(self) -> str:
            raise RuntimeError(f"boom while formatting {NEON_URL}")

    redacted = redact_secrets(LeakyAndBroken())
    assert redacted == REDACTION_FAILED
    assert "npg_Xy7pQ2" not in redacted


@pytest.mark.parametrize(
    "malformed",
    [
        "://:@",
        "https://",
        "?token",
        "?=abc",
        "&&&???",
        "https://host?token=%%%",
        "://" * 500,
        "?token=" * 500,
    ],
)
def test_malformed_input_never_raises(malformed):
    assert isinstance(redact_secrets(malformed), str)


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


def test_processor_keeps_primitive_types_intact(capsys):
    configure_logging("INFO")
    get_logger("test").info("run_finished", count=34, rate=0.85, ok=True, err=None)

    record = json.loads(capsys.readouterr().out)
    assert record["count"] == 34
    assert record["rate"] == 0.85
    assert record["ok"] is True
    assert record["err"] is None


def test_processor_redacts_an_object_passed_as_a_value(capsys):
    configure_logging("INFO")
    get_logger("test").error("run_failed", error=LeakyRepr())

    output = capsys.readouterr().out
    assert "npg_Xy7pQ2" not in output
    assert ":***@" in output


def test_processor_redacts_inside_nested_structures(capsys):
    configure_logging("INFO")
    get_logger("test").error("run_failed", context={"urls": [TURSO_URL, NEON_URL]})

    output = capsys.readouterr().out
    assert "eyJhbGciOiJFZERTQSJ9" not in output
    assert "npg_Xy7pQ2" not in output


def test_processor_survives_a_value_it_cannot_stringify(capsys):
    configure_logging("INFO")
    get_logger("test").error("run_failed", error=Unprintable())

    record = json.loads(capsys.readouterr().out)
    assert record["error"] == REDACTION_FAILED
    assert record["event"] == "run_failed"


def test_processor_survives_a_self_referencing_value(capsys):
    configure_logging("INFO")
    cycle: dict[str, object] = {"token": TURSO_URL}
    cycle["self"] = cycle
    get_logger("test").error("run_failed", context=cycle)

    output = capsys.readouterr().out
    assert output.strip(), "logging must still emit a record"
    assert "eyJhbGciOiJFZERTQSJ9" not in output
