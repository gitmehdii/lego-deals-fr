from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine

from bricks.config import Settings, get_settings
from bricks.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = REPO_ROOT / "schema.sql"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "src" / "bricks" / "db" / "migrations"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """No test may ever read the developer's real .env."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _engine(tmp_path: Path, name: str) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / f'{name}.db'}")


@pytest.fixture
def engine_from_schema_sql(tmp_path: Path) -> Engine:
    engine = _engine(tmp_path, "schema_sql")
    raw = engine.raw_connection()
    try:
        raw.cursor().executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()
    return engine


@pytest.fixture
def engine_from_models(tmp_path: Path) -> Engine:
    engine = _engine(tmp_path, "models")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def engine_from_alembic(tmp_path: Path) -> Engine:
    engine = _engine(tmp_path, "alembic")
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", str(engine.url))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return engine
