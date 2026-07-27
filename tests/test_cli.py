import pytest

from bricks.adapters.cli import catalog, health, ingest


def test_health_prints_a_page_without_crashing(capsys):
    assert health.main([]) == 0
    out = capsys.readouterr().out
    assert "Santé du pipeline" in out
    assert "Offres actives" in out


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
    assert "BRICKSET_API_KEY présent  True" in out


def test_ingest_requires_a_source():
    with pytest.raises(SystemExit):
        ingest.main([])


def test_ingest_accepts_a_source(capsys):
    assert ingest.main(["--source", "dealabs"]) == 0
    assert "source_not_implemented" in capsys.readouterr().out


def test_catalog_requires_a_subcommand():
    with pytest.raises(SystemExit):
        catalog.main([])


def test_catalog_sync_runs(capsys):
    assert catalog.main(["sync"]) == 0
    assert "catalog_not_implemented" in capsys.readouterr().out
