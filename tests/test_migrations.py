"""The lot 1 acceptance criterion, encoded."""

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

from tests.conftest import ALEMBIC_INI, MIGRATIONS_DIR

EXPECTED_TABLES = {
    "sets",
    "offers",
    "price_points",
    "alerts",
    "health_alerts",
    "runs",
}


def test_upgrade_head_creates_every_table(engine_from_alembic: Engine):
    tables = set(inspect(engine_from_alembic).get_table_names())
    assert tables >= EXPECTED_TABLES


def test_upgrade_head_stamps_the_latest_revision(engine_from_alembic: Engine):
    """Read from the migration scripts rather than hardcoded.

    A literal revision here would have to be edited by every migration, and an
    assertion that must be updated to stay green stops being an assertion.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    expected = ScriptDirectory.from_config(config).get_current_head()

    with engine_from_alembic.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar()
    assert revision == expected


def test_the_migrations_form_a_single_unbroken_chain(engine_from_alembic: Engine):
    """Two migrations with the same down_revision would silently split history."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(config)

    assert len(list(script.get_revisions("heads"))) == 1, "more than one head"
