"""Writing many rows at once over a connection that may be far away.

SQLAlchemy's executemany sends one statement per row. Against a local SQLite
file that costs nothing; against Turso it is one network round trip each, which
measured **5 rows per second** — 85 minutes for the 27 843-set catalogue, on a
workflow that gives up at 20. Folding the rows into multi-row VALUES statements
takes the same import to about a minute.

Measured against the real database, inserting into `sets`:

    rows per statement    time      extrapolated to 27 843
                     1       —                     85 min
                    50    2.40 s                   22 min
                   200    0.73 s                  1.7 min
                   500    1.17 s                  1.1 min
"""

from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

# Rows per statement. Every column of every row becomes a bound parameter, so
# this is really a parameter budget: the widest table here is `offers` at 14
# columns, giving 7 000, comfortably under SQLite's limit with room to add
# columns later. Larger chunks were measured and barely help.
CHUNK_ROWS = 500


def insert_many(session: Session, model: type, rows: list[dict[str, Any]]) -> int:
    """Insert `rows` in as few statements as the parameter budget allows.

    Every dict must carry the same keys: they become one VALUES clause.
    Caller commits, so a chunk failing halfway rolls back with the rest.
    """
    for start in range(0, len(rows), CHUNK_ROWS):
        session.execute(insert(model).values(rows[start : start + CHUNK_ROWS]))
    return len(rows)
