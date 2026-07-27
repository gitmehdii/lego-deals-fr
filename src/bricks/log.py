import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from bricks.config import LogLevel

REDACTED = "***"
REDACTION_FAILED = "<redaction failed>"

# Guards against a self-referencing structure passed as a log value.
_MAX_DEPTH = 6

# A Turso URL carries its credential as a query parameter
# (libsql://db.turso.io?authToken=...), not as a URL password, so SQLAlchemy
# does not mask it and str(exception) leaks it verbatim.
#
# Matched on the parameter name rather than on a list of known hosts: a name
# containing token, key, secret, password or pwd is redacted whatever the
# service. Over-redacting a log line costs nothing; under-redacting one is
# permanent.
_SENSITIVE_PARAM = re.compile(
    r"([?&;])([^?&;=\s]*(?:token|key|secret|password|pwd)[^?&;=\s]*)=([^&;\s\"'>)\]]*)",
    re.IGNORECASE,
)

# Postgres puts its password in the userinfo instead:
# postgresql://owner:password@ep-x.aws.neon.tech/db. The username is kept, it
# is useful when reading a log and is not the secret. Requires a scheme, so a
# bare email address in an exception message is left alone.
_URL_USERINFO = re.compile(r"(://[^/?#\s:@]+):[^/?#\s@]*@")


def redact_secrets(value: object) -> str:
    """Strip credentials out of arbitrary text. Total function: never raises.

    Every exception message goes through this before being logged or written
    to runs.error, because an exception raised by the database driver quotes
    the connection URL in full.

    Anything that goes wrong internally yields REDACTION_FAILED rather than
    the input: a redaction helper that falls back to the original value on
    error is worse than useless, and a logging processor that raises takes
    down every log line in the process.
    """
    try:
        text = value if isinstance(value, str) else str(value)
        text = _SENSITIVE_PARAM.sub(rf"\1\2={REDACTED}", text)
        return _URL_USERINFO.sub(rf"\1:{REDACTED}@", text)
    except Exception:
        return REDACTION_FAILED


def _redact_value(value: object, depth: int = 0) -> object:
    """Redact strings while leaving JSON primitives typed as they were.

    Non-primitives are stringified by the renderer anyway, so they are redacted
    here: an exception object passed as a log value carries its message, and
    that message is exactly where a connection URL shows up.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if depth >= _MAX_DEPTH:
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _redact_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(item, depth + 1) for item in value]
    return redact_secrets(value)


def _redact_processor(
    logger: object, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    try:
        return {key: _redact_value(value) for key, value in event_dict.items()}
    except Exception:
        # Drop the record rather than risk emitting an unredacted field.
        return {"event": REDACTION_FAILED}


def configure_logging(level: LogLevel = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Last before rendering, so it also covers the formatted traceback.
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level]
        ),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_run_id(run_id: int) -> None:
    """Attach run_id to every log record emitted from here on, in any module."""
    structlog.contextvars.bind_contextvars(run_id=run_id)


def clear_run_id() -> None:
    structlog.contextvars.unbind_contextvars("run_id")


@contextmanager
def run_context(run_id: int) -> Iterator[None]:
    bind_run_id(run_id)
    try:
        yield
    finally:
        clear_run_id()
