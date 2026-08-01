from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from bricks.db.models import Alert, Offer, PricePoint, Set
from bricks.services.alerts import detect_and_alert
from bricks.sources.http import SourceUnavailableError

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
CHANNEL = "test-channel"


def _set(session, set_num="10497-1", rrp=99.99, **kwargs):
    row = Set(
        set_num=set_num,
        name=kwargs.get("name", "Galaxy Explorer"),
        name_normalized="galaxy explorer",
        theme="Icons",
        year=2022,
        pieces=1254,
        rrp_eur=rrp,
        image_url="https://img.test/10497.jpg",
        updated_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _offer(session, lego_set, price=69.99, **kwargs):
    row = Offer(
        set_num=lego_set.set_num,
        resolution_score=1.0,
        resolution_method="set_number",
        source="dealabs",
        external_id=kwargs.get("external_id", "1"),
        merchant=kwargs.get("merchant", "Amazon"),
        title_raw="LEGO Icons 10497 Galaxy Explorer",
        url="https://dealabs.test/1",
        current_price_eur=price,
        first_seen_at=NOW,
        last_seen_at=NOW,
        is_active=kwargs.get("is_active", True),
    )
    session.add(row)
    session.flush()
    return row


def _history(session, offer, prices, start=NOW):
    """Earlier runs, oldest first. Strictly before the observation being judged.

    Use _observed_now for the current run's point: ingestion writes one for
    every live offer, all sharing a single `now`.
    """
    for index, price in enumerate(prices):
        session.add(
            PricePoint(
                offer_id=offer.id,
                price_eur=price,
                observed_at=start - timedelta(days=len(prices) - index),
            )
        )
    session.flush()


def _observed_now(session, offer, price, at=NOW):
    """The price point this run just wrote, the one detection must not judge
    the offer against."""
    session.add(PricePoint(offer_id=offer.id, price_eur=price, observed_at=at))
    session.flush()


class Recorder:
    def __init__(self):
        self.sent = []

    def __call__(self, payload):
        self.sent.append(payload)


def _run(session, *, send=None, min_discount_pct=25.0, only=None):
    return detect_and_alert(
        session,
        min_discount_pct=min_discount_pct,
        channel_id=CHANNEL,
        send=send,
        only_offer_ids=only,
        now=NOW,
    )


def test_a_deep_discount_is_sent_and_recorded(session):
    lego_set = _set(session)
    offer = _offer(session, lego_set, price=69.99)
    session.commit()

    recorder = Recorder()
    report, _ = _run(session, send=recorder)

    assert report.sent == 1
    (payload,) = recorder.sent
    assert payload.set_num == "10497-1"
    assert payload.reason == "discount_threshold"
    assert payload.discount_pct == pytest.approx(30.0, abs=0.01)

    alert = session.scalars(select(Alert)).one()
    assert alert.offer_id == offer.id
    assert alert.channel_id == CHANNEL
    assert alert.price_eur == pytest.approx(69.99)
    assert alert.reason == "discount_threshold"


def test_the_dry_run_touches_nothing(session):
    """TICKETS.md: --dry-run reaches neither Discord nor the alerts table."""
    lego_set = _set(session)
    _offer(session, lego_set, price=69.99)
    session.commit()

    report, previews = _run(session, send=None)

    assert len(previews) == 1, "the alert is still computed and returned"
    assert report.sent == 0
    assert session.scalar(select(func.count()).select_from(Alert)) == 0


def test_a_shallow_discount_stays_quiet(session):
    lego_set = _set(session)
    _offer(session, lego_set, price=79.99)
    session.commit()

    report, previews = _run(session, send=Recorder())
    assert (report.sent, previews) == (0, [])


def test_an_unresolved_offer_can_never_alert(session):
    """Resolution is what makes an offer comparable to a reference price."""
    lego_set = _set(session)
    offer = _offer(session, lego_set, price=10.0)
    offer.set_num = None
    session.commit()

    report, _ = _run(session, send=Recorder())
    assert report.considered == 0


def test_an_offer_without_a_price_can_never_alert(session):
    lego_set = _set(session)
    offer = _offer(session, lego_set)
    offer.current_price_eur = None
    session.commit()

    report, _ = _run(session, send=Recorder())
    assert report.considered == 0


def test_an_inactive_offer_can_never_alert(session):
    """A deal that is gone must not send anyone to a dead page."""
    lego_set = _set(session)
    _offer(session, lego_set, price=10.0, is_active=False)
    session.commit()

    report, _ = _run(session, send=Recorder())
    assert report.considered == 0


def test_only_offers_seen_this_run_are_evaluated(session):
    lego_set = _set(session)
    seen = _offer(session, lego_set, price=10.0, external_id="seen")
    _offer(session, lego_set, price=10.0, external_id="unseen")
    session.commit()

    report, _ = _run(session, send=Recorder(), only=[seen.id])
    assert report.considered == 1


def test_an_all_time_low_carries_the_record_it_beat(session):
    """SPEC.md section 6 shows the previous record on that line."""
    lego_set = _set(session, rrp=None)
    offer = _offer(session, lego_set, price=60.0)
    _history(session, offer, [80.0, 74.90, 70.0, 60.0])
    session.commit()

    _, (payload,) = _run(session, send=None)

    assert payload.reason == "all_time_low"
    assert payload.previous_low_eur == pytest.approx(70.0)
    assert payload.previous_low_at is not None


def test_two_offers_for_one_set_do_not_hide_each_others_all_time_low(session):
    """A whole run shares one timestamp, so "the set's newest point" is not
    "this offer's own point".

    Excluding the newest row overall left the other offer comparing its price
    against itself, which is never strictly lower, so its all-time low
    vanished. Fnac below is the cheapest this set has ever been.
    """
    lego_set = _set(session, rrp=None)
    amazon = _offer(session, lego_set, price=95.0, external_id="amazon")
    fnac = _offer(session, lego_set, price=40.0, external_id="fnac", merchant="Fnac")
    _history(session, amazon, [90.0, 88.0, 85.0])
    _history(session, fnac, [96.0, 95.5, 95.0])
    # Written in offer order, exactly as ingestion does it.
    _observed_now(session, amazon, 95.0)
    _observed_now(session, fnac, 40.0)
    session.commit()

    _, payloads = _run(session, send=None)

    by_merchant = {p.merchant: p for p in payloads}
    assert "Amazon" not in by_merchant, "95,00 is nobody's record"
    assert by_merchant["Fnac"].reason == "all_time_low"
    assert by_merchant["Fnac"].previous_low_eur == pytest.approx(85.0)


def test_the_judged_observation_is_excluded_by_identity_not_by_value(session):
    """Another offer sitting at the same price is still real history.

    Dropping every row equal to today's price would be the lazy fix and would
    quietly turn a tie into a record.
    """
    lego_set = _set(session, rrp=None)
    amazon = _offer(session, lego_set, price=70.0, external_id="amazon")
    fnac = _offer(session, lego_set, price=70.0, external_id="fnac", merchant="Fnac")
    _history(session, amazon, [90.0, 85.0, 80.0])
    _history(session, fnac, [95.0, 92.0, 88.0])
    _observed_now(session, amazon, 70.0)
    _observed_now(session, fnac, 70.0)
    session.commit()

    _, payloads = _run(session, send=None)

    # Each still sees the other's 70,00, and equalling a record is not beating
    # it, so neither is an all-time low.
    assert [p.reason for p in payloads] == []


def test_a_set_without_a_rrp_can_still_reach_an_all_time_low(session):
    """The severe case: with no RRP, criterion B is the only way to alert."""
    lego_set = _set(session, rrp=None)
    first = _offer(session, lego_set, price=99.0, external_id="first")
    second = _offer(
        session, lego_set, price=30.0, external_id="second", merchant="Fnac"
    )
    _history(session, first, [99.0, 99.0, 99.0])
    _history(session, second, [95.0, 90.0, 85.0])
    _observed_now(session, first, 99.0)
    _observed_now(session, second, 30.0)
    session.commit()

    _, payloads = _run(session, send=None)

    assert [(p.merchant, p.reason) for p in payloads] == [("Fnac", "all_time_low")]


def test_a_threshold_alert_carries_no_previous_record(session):
    """That line only appears when criterion B fired.

    The history has to hold something cheaper than today, otherwise this is an
    all-time low and the other branch is what gets tested.
    """
    lego_set = _set(session)
    offer = _offer(session, lego_set, price=69.99)
    _history(session, offer, [80.0, 75.0, 60.0, 69.99])
    session.commit()

    _, (payload,) = _run(session, send=None)
    assert payload.reason == "discount_threshold"
    assert payload.previous_low_eur is None


def test_no_second_alert_within_24h(session):
    """The relaunch-immediately case from the ticket."""
    lego_set = _set(session)
    _offer(session, lego_set, price=69.99)
    session.commit()

    recorder = Recorder()
    _run(session, send=recorder)
    second, _ = _run(session, send=recorder)

    assert second.sent == 0
    assert second.suppressed == 1
    assert len(recorder.sent) == 1
    assert session.scalar(select(func.count()).select_from(Alert)) == 1


def test_the_run_is_capped_at_ten_alerts(session):
    lego_set = _set(session)
    for index in range(14):
        _offer(session, lego_set, price=10.0, external_id=f"offer-{index}")
    session.commit()

    recorder = Recorder()
    report, _ = _run(session, send=recorder)

    assert report.sent == 10
    assert report.capped is True
    assert session.scalar(select(func.count()).select_from(Alert)) == 10


def test_the_cap_keeps_the_best_discounts(session):
    """A capped run must not throw away the deals worth having."""
    cheap = _set(session, set_num="1-1", rrp=100.0, name="Cheap")
    for index, price in enumerate([90.0, 10.0, 50.0]):
        _offer(session, cheap, price=price, external_id=f"o{index}")
    session.commit()

    recorder = Recorder()
    _run(session, send=recorder)

    discounts = [payload.discount_pct for payload in recorder.sent]
    assert discounts == sorted(discounts, reverse=True)


def test_a_failed_send_writes_no_alert_row(session):
    """The table means "messages we actually delivered"."""
    lego_set = _set(session)
    _offer(session, lego_set, price=69.99)
    session.commit()

    def explode(payload):
        raise RuntimeError("discord is down")

    report, _ = _run(session, send=explode)

    session.rollback()
    assert session.scalar(select(func.count()).select_from(Alert)) == 0
    assert (report.sent, report.undelivered) == (0, 1)


def test_a_failed_send_does_not_take_the_run_down(session):
    """Ingestion succeeded; Discord being unreachable does not undo that.

    The offers and price points are already durable, and an alert with no row
    is one the next run offers again, so this is reported, not raised.
    """
    lego_set = _set(session)
    _offer(session, lego_set, price=69.99)
    session.commit()

    def explode(payload):
        raise SourceUnavailableError("rate limited by discord, abandoning the run")

    report, _ = _run(session, send=explode)
    assert report.considered == 1
    assert report.undelivered == 1


def test_a_refusal_stops_the_batch_rather_than_pushing_through(session):
    """A rate limit is the likeliest reason to be here, and it asks us to stop.

    Whatever goes unsent keeps no `alerts` row, so the anti-spam rules do not
    suppress it and the next run offers it again.
    """
    lego_set = _set(session)
    for index in range(6):
        _offer(session, lego_set, price=69.99 - index, external_id=f"deal-{index}")
    session.commit()

    attempts: list[int] = []

    def discord_rate_limits_after_two(payload):
        attempts.append(payload.offer_id)
        if len(attempts) > 2:
            raise SourceUnavailableError("rate limited by discord")

    report, _ = _run(session, send=discord_rate_limits_after_two)

    assert len(attempts) == 3, "stopped at the first refusal, not after trying all six"
    assert report.sent == 2
    assert report.undelivered == 4
    assert session.scalar(select(func.count()).select_from(Alert)) == 2


def test_the_payload_carries_what_the_message_needs(session):
    lego_set = _set(session)
    _offer(session, lego_set, price=69.99)
    session.commit()

    _, (payload,) = _run(session, send=None)

    assert payload.set_name == "Galaxy Explorer"
    assert payload.merchant == "Amazon"
    assert payload.pieces == 1254
    assert payload.theme == "Icons"
    assert payload.year == 2022
    assert payload.image_url == "https://img.test/10497.jpg"
    assert payload.url == "https://dealabs.test/1"
