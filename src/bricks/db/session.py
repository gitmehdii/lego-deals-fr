from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from bricks.config import Settings, get_settings

# Turso's own spelling of the credential in a connection URL.
_AUTH_TOKEN = "authToken"


def create_db_engine(settings: Settings | None = None) -> Engine:
    """The engine, with a Turso token moved where the driver actually reads it.

    sqlalchemy-libsql forwards `authToken` inside the connection URL, but
    libsql-experimental takes it as an `auth_token` argument that it defaults
    to `""`. The two never meet and Turso answers 401, "empty JWT token".

    The token is *moved*, not copied: left in the query string as well, the
    driver builds a route Turso answers 404 to. Moving it also keeps the
    credential out of `engine.url`, so it survives in one place only.
    """
    settings = settings or get_settings()
    return engine_for_url(settings.database_url)


def engine_for_url(database_url: str, **kwargs: object) -> Engine:
    """Every engine in the project is built here, migrations included.

    Alembic used to assemble its own with engine_from_config and so missed the
    token handling above, which meant `alembic upgrade head` — the first
    command any deployment runs — was the one thing that could not reach Turso.
    """
    url = make_url(database_url)

    token = _auth_token(url)
    if token is None:
        return create_engine(url, **kwargs)
    return create_engine(
        url.difference_update_query([_AUTH_TOKEN]),
        connect_args={"auth_token": token},
        **kwargs,
    )


def _auth_token(url: URL) -> str | None:
    """The token a remote libSQL URL carries, if it carries one."""
    if "libsql" not in url.drivername or not url.host:
        return None
    value = url.query.get(_AUTH_TOKEN)
    if isinstance(value, tuple):
        value = value[-1] if value else None
    return value or None


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
