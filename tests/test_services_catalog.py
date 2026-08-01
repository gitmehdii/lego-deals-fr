from sqlalchemy import select

from bricks.db.models import Set
from bricks.services.catalog import sync_catalogue
from bricks.sources.models import CatalogSet, RetailPrice

GALAXY = CatalogSet(
    set_num="10497-1",
    name="Galaxy Explorer",
    theme="Icons",
    year=2022,
    pieces=1254,
    image_url="https://img.test/10497-1.jpg",
)
FALCON = CatalogSet(
    set_num="75192-1", name="Millennium Falcon", theme="Star Wars", year=2017
)


class FakeRebrickable:
    def __init__(self, sets: list[CatalogSet]) -> None:
        self._sets = sets

    def fetch(self) -> list[CatalogSet]:
        return list(self._sets)


class FakeBrickset:
    def __init__(self, by_year: dict[int, list[RetailPrice]]) -> None:
        self._by_year = by_year
        self.years_called: list[int] = []

    def fetch_retail_prices(self, year: int) -> list[RetailPrice]:
        self.years_called.append(year)
        return list(self._by_year.get(year, []))


def _sync(session, sets, prices=None, **kwargs):
    return sync_catalogue(
        session,
        rebrickable=FakeRebrickable(sets),
        brickset=None if prices is None else FakeBrickset(prices),
        **kwargs,
    )


def _row(session, set_num="10497-1") -> Set:
    return session.scalars(select(Set).where(Set.set_num == set_num)).one()


def test_first_sync_inserts_the_catalogue(session):
    report = _sync(session, [GALAXY, FALCON])

    assert (report.sets_fetched, report.sets_created, report.sets_updated) == (2, 2, 0)
    row = _row(session)
    assert row.name == "Galaxy Explorer"
    assert row.name_normalized == "galaxy explorer"
    assert row.theme == "Icons"
    assert row.year == 2022
    assert row.pieces == 1254
    assert row.image_url == "https://img.test/10497-1.jpg"
    assert row.rrp_eur is None


def test_running_twice_changes_nothing(session):
    """The lot's acceptance criterion: a second sync is a no-op."""
    _sync(session, [GALAXY, FALCON])
    before = {row.set_num: row.updated_at for row in session.scalars(select(Set))}
    session.expire_all()

    report = _sync(session, [GALAXY, FALCON])

    assert (report.sets_created, report.sets_updated) == (0, 0)
    assert session.scalar(select(Set.set_num).where(Set.set_num == "10497-1"))
    after = {row.set_num: row.updated_at for row in session.scalars(select(Set))}
    assert after == before, "updated_at moved without anything actually changing"
    assert len(after) == 2


def test_a_renamed_set_is_updated_and_renormalised(session):
    _sync(session, [GALAXY])
    session.expire_all()

    renamed = GALAXY.model_copy(update={"name": "Galaxy Explorer (Café Edition)"})
    report = _sync(session, [renamed])

    assert (report.sets_created, report.sets_updated) == (0, 1)
    row = _row(session)
    assert row.name == "Galaxy Explorer (Café Edition)"
    assert row.name_normalized == "galaxy explorer cafe edition"


def test_an_identity_sync_never_wipes_a_known_price(session):
    """Rebrickable has no idea what a RRP is; it must not be able to clear one."""
    _sync(session, [GALAXY], {2022: [RetailPrice(set_num="10497-1", rrp_eur=99.99)]})
    session.expire_all()

    _sync(session, [GALAXY.model_copy(update={"pieces": 1255})])

    session.expire_all()
    assert _row(session).rrp_eur == 99.99


def test_the_price_phase_fills_rrp_in_euros(session):
    report = _sync(
        session, [GALAXY], {2022: [RetailPrice(set_num="10497-1", rrp_eur=99.99)]}
    )

    assert report.rrp_updated == 1
    assert report.rrp_skipped is False
    assert _row(session).rrp_eur == 99.99


def test_the_price_phase_touches_nothing_but_the_price(session):
    _sync(session, [GALAXY])
    session.expire_all()

    _sync(session, [GALAXY], {2022: [RetailPrice(set_num="10497-1", rrp_eur=99.99)]})

    session.expire_all()
    row = _row(session)
    assert (row.name, row.theme, row.year, row.pieces) == (
        "Galaxy Explorer",
        "Icons",
        2022,
        1254,
    )


def test_a_set_without_a_euro_price_keeps_rrp_null(session):
    report = _sync(
        session, [GALAXY], {2022: [RetailPrice(set_num="10497-1", rrp_eur=None)]}
    )

    assert (report.rrp_unknown, report.rrp_updated) == (1, 0)
    assert _row(session).rrp_eur is None


def test_a_price_that_disappears_upstream_does_not_erase_the_one_we_hold(session):
    _sync(session, [GALAXY], {2022: [RetailPrice(set_num="10497-1", rrp_eur=99.99)]})
    session.expire_all()

    report = _sync(
        session, [GALAXY], {2022: [RetailPrice(set_num="10497-1", rrp_eur=None)]}
    )

    assert report.rrp_unknown == 1
    session.expire_all()
    assert _row(session).rrp_eur == 99.99


def test_an_unchanged_price_is_not_rewritten(session):
    prices = {2022: [RetailPrice(set_num="10497-1", rrp_eur=99.99)]}
    _sync(session, [GALAXY], prices)
    session.expire_all()
    before = _row(session).updated_at
    session.expire_all()

    report = _sync(session, [GALAXY], prices)

    assert report.rrp_updated == 0
    session.expire_all()
    assert _row(session).updated_at == before


def test_a_set_brickset_knows_and_we_do_not_is_counted_not_inserted(session):
    report = _sync(
        session, [GALAXY], {2022: [RetailPrice(set_num="99999-1", rrp_eur=10.0)]}
    )

    assert report.rrp_unmatched == 1
    assert session.scalar(select(Set.set_num).where(Set.set_num == "99999-1")) is None


def test_only_years_present_in_the_catalogue_are_queried(session):
    brickset = FakeBrickset({})
    sync_catalogue(
        session, rebrickable=FakeRebrickable([GALAXY, FALCON]), brickset=brickset
    )
    assert brickset.years_called == [2017, 2022]


def test_since_year_narrows_the_price_phase(session):
    brickset = FakeBrickset({})
    report = sync_catalogue(
        session,
        rebrickable=FakeRebrickable([GALAXY, FALCON]),
        brickset=brickset,
        since_year=2020,
    )
    assert brickset.years_called == [2022]
    assert report.rrp_years == 1


def test_a_set_without_a_year_does_not_produce_a_null_year_query(session):
    brickset = FakeBrickset({})
    undated = CatalogSet(set_num="1-1", name="Undated", year=None)
    sync_catalogue(session, rebrickable=FakeRebrickable([undated]), brickset=brickset)
    assert brickset.years_called == []


def test_without_brickset_the_price_phase_is_skipped(session):
    report = _sync(session, [GALAXY])

    assert report.rrp_skipped is True
    assert report.sets_created == 1
    assert _row(session).rrp_eur is None
