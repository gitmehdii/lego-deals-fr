"""schema.sql is the reference document. The models and the Alembic migration
must produce a byte-for-byte equivalent database, and nobody should have to
verify that by reading three files side by side.

Both sides are reflected from a real SQLite database, so the comparison is on
what SQLite actually stored, not on how the DDL happened to be written.
"""

import pytest
from sqlalchemy import Engine, inspect

EXPECTED_TABLES = {"sets", "offers", "price_points", "alerts", "runs"}


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip().strip("()").strip().strip("'")


def _has_autoincrement(engine: Engine, table: str) -> bool:
    with engine.connect() as connection:
        sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).scalar()
    return "AUTOINCREMENT" in (sql or "").upper()


def describe(engine: Engine) -> dict[str, dict[str, object]]:
    inspector = inspect(engine)
    tables = [
        name
        for name in inspector.get_table_names()
        if not name.startswith("sqlite_") and name != "alembic_version"
    ]

    described: dict[str, dict[str, object]] = {}
    for table in sorted(tables):
        primary_key = tuple(inspector.get_pk_constraint(table)["constrained_columns"])
        described[table] = {
            # Ordered: a column inserted in the wrong position is a difference.
            #
            # A primary key column is reported as nullable when SQLite wrote the
            # DDL without NOT NULL, which it does for `INTEGER PRIMARY KEY` and
            # for `TEXT PRIMARY KEY` alike. That flag says nothing useful, so it
            # is normalised away rather than compared.
            "columns": [
                (
                    column["name"],
                    str(column["type"]),
                    False if column["name"] in primary_key else column["nullable"],
                    _normalize_default(column["default"]),
                )
                for column in inspector.get_columns(table)
            ],
            "primary_key": primary_key,
            "autoincrement": _has_autoincrement(engine, table),
            "indexes": sorted(
                (
                    index["name"],
                    tuple(index["column_names"]),
                    bool(index["unique"]),
                )
                for index in inspector.get_indexes(table)
            ),
            "unique_constraints": sorted(
                (
                    constraint["name"],
                    tuple(constraint["column_names"]),
                )
                for constraint in inspector.get_unique_constraints(table)
            ),
            "foreign_keys": sorted(
                (
                    fk["name"],
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                )
                for fk in inspector.get_foreign_keys(table)
            ),
        }
    return described


@pytest.fixture
def reference(engine_from_schema_sql: Engine) -> dict[str, dict[str, object]]:
    return describe(engine_from_schema_sql)


def test_schema_sql_defines_the_five_tables(reference):
    assert set(reference) == EXPECTED_TABLES


def test_models_match_schema_sql(reference, engine_from_models):
    assert describe(engine_from_models) == reference


def test_migration_matches_schema_sql(reference, engine_from_alembic):
    assert describe(engine_from_alembic) == reference


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("sets", "updated_at"),
        ("offers", "first_seen_at"),
        ("offers", "last_seen_at"),
        ("price_points", "observed_at"),
        ("alerts", "sent_at"),
        ("runs", "started_at"),
        ("runs", "finished_at"),
    ],
)
def test_timestamps_are_stored_as_text(reference, table, column):
    types = {name: sql_type for name, sql_type, _, _ in reference[table]["columns"]}
    assert types[column] == "TEXT"


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("sets", "rrp_eur"),
        ("offers", "resolution_score"),
        ("offers", "current_price_eur"),
        ("price_points", "price_eur"),
        ("alerts", "price_eur"),
        ("alerts", "discount_pct"),
    ],
)
def test_prices_are_stored_as_real(reference, table, column):
    types = {name: sql_type for name, sql_type, _, _ in reference[table]["columns"]}
    assert types[column] == "REAL"
