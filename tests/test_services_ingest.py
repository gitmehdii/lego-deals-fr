from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from bricks.db.models import Offer, PricePoint, Run
from bricks.services.ingest import ingest
from bricks.sources.models import RawOffer

DEAL = RawOffer(
    external_id="3383357",
    title="Lego Technic 42231 - Dodge Charger",
    url="https://www.dealabs.com/bons-plans/lego-technic-42231-3383357",
    price_eur=115.90,
    merchant="Alternate",
    published_at=datetime(2026, 7, 28, 11, 28, 31, tzinfo=UTC),
)


class FakeSource:
    name = "dealabs"

    def __init__(self, offers, error=None):
        self._offers = offers
        self._error = error
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._offers)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _run(session) -> Run:
    return session.scalars(select(Run).order_by(Run.id.desc())).first()


def test_a_new_offer_lands_with_its_price_point(session):
    report = ingest(session, FakeSource([DEAL]))

    assert (report.items_found, report.items_new, report.price_points) == (1, 1, 1)
    offer = session.scalars(select(Offer)).one()
    assert offer.source == "dealabs"
    assert offer.external_id == "3383357"
    assert offer.title_raw == "Lego Technic 42231 - Dodge Charger"
    assert offer.merchant == "Alternate"
    assert offer.current_price_eur == pytest.approx(115.90)
    assert offer.is_active is True

    point = session.scalars(select(PricePoint)).one()
    assert point.offer_id == offer.id
    assert point.price_eur == pytest.approx(115.90)


def test_an_offer_arrives_unresolved(session):
    """Resolution is lot 4. An unresolved offer never produces an alert."""
    ingest(session, FakeSource([DEAL]))
    offer = session.scalars(select(Offer)).one()
    assert offer.set_num is None
    assert offer.resolution_score is None
    assert offer.resolution_method is None


def test_a_second_run_adds_no_offer_but_does_add_a_price_point(session):
    """The acceptance criterion of the ticket, in one test."""
    ingest(session, FakeSource([DEAL]))
    second = ingest(session, FakeSource([DEAL]))

    assert second.items_new == 0
    assert _count(session, Offer) == 1
    assert _count(session, PricePoint) == 2


def test_an_unchanged_price_is_still_recorded(session):
    """ "We looked and it was still 115,90" is itself information."""
    ingest(session, FakeSource([DEAL]))
    ingest(session, FakeSource([DEAL]))

    prices = session.scalars(select(PricePoint.price_eur)).all()
    assert prices == [pytest.approx(115.90), pytest.approx(115.90)]


def test_a_price_change_updates_the_offer_and_appends_history(session):
    ingest(session, FakeSource([DEAL]))
    cheaper = DEAL.model_copy(update={"price_eur": 99.99})
    ingest(session, FakeSource([cheaper]))

    session.expire_all()
    offer = session.scalars(select(Offer)).one()
    assert offer.current_price_eur == pytest.approx(99.99)
    assert sorted(session.scalars(select(PricePoint.price_eur))) == [
        pytest.approx(99.99),
        pytest.approx(115.90),
    ]


def test_an_offer_without_a_price_is_stored_but_makes_no_price_point(session):
    """price_points.price_eur is NOT NULL, and a guessed price is worse."""
    report = ingest(session, FakeSource([DEAL.model_copy(update={"price_eur": None})]))

    assert (report.items_new, report.price_points) == (1, 0)
    assert _count(session, Offer) == 1
    assert _count(session, PricePoint) == 0


def test_a_price_that_disappears_does_not_erase_the_last_one_known(session):
    ingest(session, FakeSource([DEAL]))
    ingest(session, FakeSource([DEAL.model_copy(update={"price_eur": None})]))

    session.expire_all()
    assert session.scalars(select(Offer)).one().current_price_eur == pytest.approx(
        115.90
    )


def test_the_raw_title_is_never_overwritten(session):
    """When resolution misbehaves, this is the evidence of what it was given."""
    ingest(session, FakeSource([DEAL]))
    ingest(session, FakeSource([DEAL.model_copy(update={"title": "titre réécrit"})]))

    session.expire_all()
    assert session.scalars(select(Offer)).one().title_raw == DEAL.title


def test_the_same_deal_listed_twice_in_one_feed_does_not_break_the_run(session):
    report = ingest(session, FakeSource([DEAL, DEAL]))

    assert report.items_found == 1
    assert _count(session, Offer) == 1
    assert _run(session).status == "ok"


def test_a_successful_run_is_traced_with_its_counters(session):
    ingest(session, FakeSource([DEAL]))

    run = _run(session)
    assert run.source == "dealabs"
    assert run.status == "ok"
    assert run.items_found == 1
    assert run.items_new == 1
    assert run.items_resolved == 0
    assert run.finished_at is not None
    assert run.error is None


def test_two_runs_leave_two_rows_in_status_ok(session):
    ingest(session, FakeSource([DEAL]))
    ingest(session, FakeSource([DEAL]))

    statuses = session.scalars(select(Run.status).order_by(Run.id)).all()
    assert statuses == ["ok", "ok"]


def test_a_crashing_source_still_writes_its_run_row(session):
    """A pipeline whose failures are invisible is one nobody notices has died."""
    with pytest.raises(RuntimeError):
        ingest(session, FakeSource([], error=RuntimeError("parser exploded")))

    run = _run(session)
    assert run.status == "error"
    assert "parser exploded" in run.error
    assert run.finished_at is not None


def test_a_crash_never_writes_a_credential_into_runs_error(session):
    """runs.error receives raw exception text; CLAUDE.md requires it redacted."""
    boom = RuntimeError("connect failed: libsql://db.turso.io?authToken=s3cret")
    with pytest.raises(RuntimeError):
        ingest(session, FakeSource([], error=boom))

    assert "s3cret" not in _run(session).error


def test_an_empty_feed_is_a_successful_run_that_found_nothing(session):
    """Lot 6 alerts on three consecutive empty runs; one is not a failure."""
    report = ingest(session, FakeSource([]))

    assert report.items_found == 0
    run = _run(session)
    assert run.status == "ok"
    assert run.items_found == 0


def test_first_seen_at_prefers_the_publication_date(session):
    ingest(session, FakeSource([DEAL]))
    offer = session.scalars(select(Offer)).one()
    assert offer.first_seen_at == DEAL.published_at


def test_first_seen_at_does_not_move_on_later_runs(session):
    ingest(session, FakeSource([DEAL]))
    first = session.scalars(select(Offer)).one().first_seen_at
    session.expire_all()

    ingest(session, FakeSource([DEAL]))
    session.expire_all()
    offer = session.scalars(select(Offer)).one()
    assert offer.first_seen_at == first
    assert offer.last_seen_at > first
