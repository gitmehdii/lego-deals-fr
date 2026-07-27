import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from bricks.config import LogLevel

REDACTED = "***"

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


def redact_secrets(text: str) -> str:
    """Strip credential-bearing query parameters out of arbitrary text.

    Every exception message goes through this before being logged or written
    to runs.error, because an exception raised by the database driver quotes
    the connection URL in full.
    """
    return _SENSITIVE_PARAM.sub(rf"\1\2={REDACTED}", text)


def _redact_processor(
    logger: object, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    return {
        key: redact_secrets(value) if isinstance(value, str) else value
        for key, value in event_dict.items()
    }


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
