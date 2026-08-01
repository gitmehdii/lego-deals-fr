"""How a connection URL becomes an engine.

One rule is doing the work here: sqlalchemy-libsql forwards `authToken` inside
the connection URL, while libsql-experimental takes it as an `auth_token`
argument defaulting to `""`. Against a real Turso database that combination
answers 401, "empty JWT token".
"""

import pytest
from sqlalchemy.engine import make_url

from bricks.config import Settings
from bricks.db.session import _auth_token, create_db_engine, engine_for_url

TURSO_URL = "sqlite+libsql://db.turso.test/?authToken=s3cret-jwt&secure=true"


def _settings(url: str) -> Settings:
    return Settings(_env_file=None, database_url=url)


def test_a_turso_token_leaves_the_url_for_connect_args():
    """Moved, not copied.

    Left in the query string as well, the driver builds a route Turso answers
    404 to — so this is not tidiness, it is the difference between working and
    not.
    """
    engine = create_db_engine(_settings(TURSO_URL))

    assert "s3cret-jwt" not in str(engine.url)
    assert "s3cret-jwt" not in repr(engine.url)
    assert engine.url.query["secure"] == "true", "the rest of the query survives"


def test_the_token_is_still_what_reaches_the_driver():
    assert _auth_token(make_url(TURSO_URL)) == "s3cret-jwt"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///local.db",
        # A local file through the libSQL driver never authenticates.
        "sqlite+libsql:///local.db",
    ],
)
def test_a_url_without_a_token_is_left_alone(url):
    assert _auth_token(make_url(url)) is None
    assert str(create_db_engine(_settings(url)).url) == url


def test_a_plain_sqlite_engine_still_works(tmp_path):
    """The local path is the one every test and every dev run takes."""
    from sqlalchemy import text

    engine = engine_for_url(f"sqlite:///{tmp_path / 'probe.db'}")
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar() == 1


def test_extra_engine_arguments_are_passed_through(tmp_path):
    """Alembic needs poolclass=NullPool, and goes through the same helper."""
    from sqlalchemy.pool import NullPool

    engine = engine_for_url(f"sqlite:///{tmp_path / 'probe.db'}", poolclass=NullPool)
    assert isinstance(engine.pool, NullPool)
