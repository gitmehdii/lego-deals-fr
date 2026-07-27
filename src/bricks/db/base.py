from datetime import datetime

from sqlalchemy import REAL, Integer, MetaData, Text
from sqlalchemy.orm import DeclarativeBase

from bricks.db.types import IntBoolean, UtcDateTime

# The standard SQLAlchemy convention. Every constraint carries a predictable
# name, which is what lets Alembic alter one by name later instead of guessing
# what SQLite called it.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base whose type map emits exactly the SQL of schema.sql."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012
        bool: IntBoolean,
        datetime: UtcDateTime,
        float: REAL,
        int: Integer,
        str: Text,
    }
