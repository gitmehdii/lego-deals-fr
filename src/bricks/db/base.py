from datetime import datetime

from sqlalchemy import REAL, Integer, Text
from sqlalchemy.orm import DeclarativeBase

from bricks.db.types import IntBoolean, UtcDateTime


class Base(DeclarativeBase):
    """Declarative base whose type map emits exactly the SQL of schema.sql.

    No MetaData naming convention on purpose: schema.sql leaves its foreign
    keys and its UNIQUE constraint unnamed, and a convention would silently
    name them, breaking the fidelity test.
    """

    type_annotation_map = {  # noqa: RUF012
        bool: IntBoolean,
        datetime: UtcDateTime,
        float: REAL,
        int: Integer,
        str: Text,
    }
