"""Chunked inserts. The behaviour that matters is "same rows, fewer statements"."""

from datetime import UTC, datetime

from sqlalchemy import event, func, select

from bricks.db.bulk import CHUNK_ROWS, insert_many
from bricks.db.models import Set

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _rows(count: int) -> list[dict]:
    return [
        {
            "set_num": f"{10000 + index}-1",
            "name": f"Set {index}",
            "name_normalized": f"set {index}",
            "theme": "Icons",
            "year": 2026,
            "pieces": 100,
            "rrp_eur": None,
            "image_url": None,
            "updated_at": NOW,
        }
        for index in range(count)
    ]


def _count_statements(session) -> list[str]:
    seen: list[str] = []
    event.listen(
        session.bind,
        "before_cursor_execute",
        lambda conn, cursor, statement, *rest: seen.append(statement),
    )
    return seen


def test_every_row_arrives(session):
    insert_many(session, Set, _rows(CHUNK_ROWS * 2 + 7))
    session.commit()

    assert session.scalar(select(func.count()).select_from(Set)) == CHUNK_ROWS * 2 + 7


def test_the_rows_keep_their_values(session):
    insert_many(session, Set, _rows(3))
    session.commit()

    row = session.get(Set, "10001-1")
    assert (row.name, row.theme, row.updated_at) == ("Set 1", "Icons", NOW)


def test_one_statement_per_chunk_not_per_row(session):
    """The whole point: executemany costs a round trip each, and against Turso
    that measured 5 rows a second."""
    statements = _count_statements(session)
    insert_many(session, Set, _rows(CHUNK_ROWS + 1))
    session.commit()

    inserts = [t for t in statements if t.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 2, f"expected 2 chunks, got {len(inserts)}"


def test_an_empty_list_is_not_a_statement(session):
    statements = _count_statements(session)
    assert insert_many(session, Set, []) == 0
    assert not [t for t in statements if t.lstrip().upper().startswith("INSERT")]
