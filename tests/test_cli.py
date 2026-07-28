import gzip

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bricks.adapters.cli import catalog, health, ingest
from bricks.db.base import Base
from bricks.db.models import Set
from bricks.db.session import create_db_engine
from bricks.sources.http import HttpFetcher

SETS_CSV = "\n".join(
    [
        "set_num,name,year,theme_id,num_parts,img_url",
        "10497-1,Galaxy Explorer,2022,721,1254,https://img.test/10497-1.jpg",
    ]
)
THEMES_CSV = "\n".join(["id,name,parent_id", "721,Icons,"])


# One Brickset getSets page, trimmed to the fields the client reads.
BRICKSET_PAGE = {
    "status": "success",
    "matches": 1,
    "sets": [
        {
            "number": "10497",
            "numberVariant": 1,
            "LEGOCom": {"US": {"retailPrice": 99.99}, "DE": {"retailPrice": 89.99}},
        }
    ],
}


@pytest.fixture
def catalogue_network(monkeypatch, tmp_path):
    """A local database and a fake internet. No test ever reaches the real one.

    Builds the schema straight from the URL rather than through
    create_db_engine(): get_settings is cached, so reading it here would pin
    the environment as it stands now and a test could no longer set
    BRICKSET_API_KEY afterwards.
    """
    database_url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    Base.metadata.create_all(create_engine(database_url))
    requests = []

    def handler(request):
        requests.append(request)
        url = str(request.url)
        if "brickset" in url:
            return httpx.Response(200, json=BRICKSET_PAGE)
        body = THEMES_CSV if "themes" in url else SETS_CSV
        return httpx.Response(200, content=gzip.compress(body.encode()))

    def fake_fetcher(*args, **kwargs):
        return HttpFetcher(
            httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
        )

    monkeypatch.setattr(catalog, "HttpFetcher", fake_fetcher)
    return requests


def test_health_prints_a_page_without_crashing(capsys):
    assert health.main([]) == 0
    out = capsys.readouterr().out
    assert "bricks pipeline health" in out
    assert "Active offers" in out


def test_health_never_prints_the_database_url(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "sqlite+libsql://db.turso.test?authToken=s3cret")
    assert health.main([]) == 0
    out = capsys.readouterr().out
    assert "s3cret" not in out
    assert "db.turso.test" not in out


def test_health_reports_secret_presence_not_value(monkeypatch, capsys):
    monkeypatch.setenv("BRICKSET_API_KEY", "s3cret")
    assert health.main([]) == 0
    out = capsys.readouterr().out
    assert "s3cret" not in out
    assert "BRICKSET_API_KEY set       True" in out


def test_ingest_requires_a_source():
    with pytest.raises(SystemExit):
        ingest.main([])


def test_ingest_accepts_a_source(capsys):
    assert ingest.main(["--source", "dealabs"]) == 0
    assert "source_not_implemented" in capsys.readouterr().out


def test_catalog_requires_a_subcommand():
    with pytest.raises(SystemExit):
        catalog.main([])


def test_catalog_sync_imports_the_catalogue(catalogue_network, capsys):
    assert catalog.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "Sets created               1" in out


def test_catalog_sync_is_idempotent(catalogue_network, capsys):
    assert catalog.main(["sync"]) == 0
    capsys.readouterr()

    assert catalog.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "Sets created               0" in out
    assert "Sets updated               0" in out


def test_catalog_sync_skips_prices_without_an_api_key(catalogue_network, capsys):
    """No Brickset account is not an error: the identity import still runs."""
    assert catalog.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "brickset_api_key_missing" in out
    assert "Retail prices              skipped" in out


def test_catalog_sync_skip_rrp_never_asks_for_a_key(
    monkeypatch, catalogue_network, capsys
):
    monkeypatch.setenv("BRICKSET_API_KEY", "s3cret")
    assert catalog.main(["sync", "--skip-rrp"]) == 0
    out = capsys.readouterr().out
    assert "s3cret" not in out
    assert "Retail prices              skipped" in out


def test_catalog_sync_fills_the_rrp_when_a_key_is_present(
    monkeypatch, catalogue_network, capsys
):
    """The whole command, both providers, through the real client wiring."""
    monkeypatch.setenv("BRICKSET_API_KEY", "s3cret")

    assert catalog.main(["sync"]) == 0

    out = capsys.readouterr().out
    assert "Prices updated           1" in out
    assert "s3cret" not in out

    engine = create_db_engine()
    with Session(engine) as session:
        row = session.scalars(select(Set).where(Set.set_num == "10497-1")).one()
    assert row.rrp_eur == 89.99
    assert row.name_normalized == "galaxy explorer"
    assert row.theme == "Icons"


def test_catalog_sync_never_puts_the_api_key_in_a_url(
    monkeypatch, catalogue_network, capsys
):
    monkeypatch.setenv("BRICKSET_API_KEY", "s3cret")
    assert catalog.main(["sync"]) == 0

    brickset_calls = [r for r in catalogue_network if "brickset" in str(r.url)]
    assert brickset_calls, "the Brickset phase never ran"
    for request in brickset_calls:
        assert request.method == "POST"
        assert "s3cret" not in str(request.url)


def test_catalog_sync_since_year_skips_older_catalogue_years(
    monkeypatch, catalogue_network, capsys
):
    monkeypatch.setenv("BRICKSET_API_KEY", "s3cret")
    assert catalog.main(["sync", "--since-year", "2030"]) == 0

    assert "Years queried            0" in capsys.readouterr().out
    assert not [r for r in catalogue_network if "brickset" in str(r.url)]


def test_catalog_sync_reports_a_failing_source_without_crashing(
    monkeypatch, catalogue_network, capsys
):
    def broken_fetcher(*args, **kwargs):
        return HttpFetcher(
            httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(503))
            ),
            sleep=lambda _: None,
        )

    monkeypatch.setattr(catalog, "HttpFetcher", broken_fetcher)
    assert catalog.main(["sync"]) == 1
    assert "catalog_sync_failed" in capsys.readouterr().out
