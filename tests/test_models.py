from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import InvalidRequestError, StatementError
from sqlalchemy.orm import Session, selectinload

from bricks.db.models import Offer, PricePoint, Set

PARIS_SUMMER = timezone(timedelta(hours=2))


def make_offer(**overrides) -> Offer:
    defaults = {
        "source": "dealabs",
        "external_id": "abc123",
        "title_raw": "LEGO Icons 10497 Galaxy Explorer à 79,99€ - Amazon",
        "url": "https://dealabs.test/deal/abc123",
        "first_seen_at": datetime(2026, 7, 27, 5, 0, tzinfo=UTC),
        "last_seen_at": datetime(2026, 7, 27, 5, 0, tzinfo=UTC),
    }
    return Offer(**(defaults | overrides))


@pytest.fixture
def session(engine_from_models: Engine):
    with Session(engine_from_models) as session:
        yield session


def test_timestamps_round_trip_as_utc(session):
    session.add(make_offer())
    session.commit()
    session.expunge_all()

    offer = session.scalars(select(Offer)).one()
    assert offer.first_seen_at == datetime(2026, 7, 27, 5, 0, tzinfo=UTC)
    assert offer.first_seen_at.tzinfo is UTC


def test_non_utc_input_is_normalised_to_utc(session):
    session.add(
        make_offer(first_seen_at=datetime(2026, 7, 27, 7, 0, tzinfo=PARIS_SUMMER))
    )
    session.commit()
    session.expunge_all()

    offer = session.scalars(select(Offer)).one()
    assert offer.first_seen_at == datetime(2026, 7, 27, 5, 0, tzinfo=UTC)


def test_timestamps_are_stored_as_iso_8601_text(session):
    session.add(make_offer())
    session.commit()

    stored = (
        session.connection()
        .exec_driver_sql("SELECT first_seen_at FROM offers")
        .scalar()
    )
    assert stored == "2026-07-27T05:00:00+00:00"


def test_naive_datetime_is_rejected(session):
    session.add(make_offer(first_seen_at=datetime(2026, 7, 27, 5, 0)))  # noqa: DTZ001
    with pytest.raises(StatementError, match="naive datetime rejected"):
        session.commit()


def test_is_active_round_trips_as_a_bool(session):
    session.add(make_offer())
    session.commit()
    session.expunge_all()

    offer = session.scalars(select(Offer)).one()
    assert offer.is_active is True


def test_relationships_never_load_implicitly(session):
    """lazy="raise" everywhere: a batch pipeline must not emit surprise queries."""
    session.add(make_offer())
    session.commit()
    session.expunge_all()

    offer = session.scalars(select(Offer)).one()
    for attribute in ("set", "price_points", "alerts"):
        with pytest.raises(InvalidRequestError, match="lazy='raise'"):
            getattr(offer, attribute)


def test_relationships_load_when_asked_explicitly(session):
    lego_set = Set(
        set_num="10497-1",
        name="Galaxy Explorer",
        name_normalized="galaxy explorer",
        updated_at=datetime(2026, 7, 27, tzinfo=UTC),
    )
    offer = make_offer(set_num="10497-1")
    session.add_all([lego_set, offer])
    session.flush()
    session.add(
        PricePoint(
            offer_id=offer.id,
            price_eur=79.99,
            observed_at=datetime(2026, 7, 27, 5, 0, tzinfo=UTC),
        )
    )
    session.commit()
    session.expunge_all()

    loaded = session.scalars(
        select(Offer).options(selectinload(Offer.set), selectinload(Offer.price_points))
    ).one()
    assert loaded.set.name == "Galaxy Explorer"
    assert [point.price_eur for point in loaded.price_points] == [79.99]
