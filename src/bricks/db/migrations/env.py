from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from bricks.adapters.cli.common import load_settings
from bricks.db.base import Base
from bricks.db.models import Alert, Offer, PricePoint, Run, Set  # noqa: F401
from bricks.db.session import engine_for_url

config = context.config

# Tests set configure_logger to False: fileConfig() disables existing loggers
# and would leak that side effect into the rest of the suite.
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """The same clean report as the CLI, not a traceback.

    `alembic upgrade head` runs before every command in the workflows, so it
    is the first thing to meet a freshly pasted DATABASE_URL and the first
    place a wrong one shows up.
    """
    if configured := config.get_main_option("sqlalchemy.url"):
        return configured
    settings = load_settings()
    if settings is None:
        raise SystemExit(2)
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Built through engine_for_url rather than engine_from_config so that a
    # Turso URL reaches the driver the same way here as everywhere else.
    connectable = engine_for_url(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
