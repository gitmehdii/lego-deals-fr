from datetime import UTC, datetime

from sqlalchemy import Dialect, Integer, Text
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Aware datetime in Python, ISO 8601 UTC string in the database.

    schema.sql stores every timestamp as TEXT. Naive datetimes are rejected at
    the boundary rather than silently assumed to be UTC.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"naive datetime rejected, timestamps must be aware: {value!r}"
            )
        return value.astimezone(UTC).isoformat()

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value).astimezone(UTC)


class IntBoolean(TypeDecorator[bool]):
    """bool in Python, INTEGER 0/1 in the database, as schema.sql mandates."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: bool | None, dialect: Dialect) -> int | None:
        return None if value is None else int(value)

    def process_result_value(self, value: int | None, dialect: Dialect) -> bool | None:
        return None if value is None else bool(value)
