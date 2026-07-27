import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

from bricks.config import LogLevel


def configure_logging(level: LogLevel = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
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
