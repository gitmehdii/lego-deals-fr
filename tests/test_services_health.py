from datetime import UTC, datetime, timedelta

import pytest

from bricks.db.models import Alert, Offer, Run, Set
from bricks.services.health import collect_health

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def _run(session, *, status="ok", found=30, source="dealabs", ago_hours=1):
    row = Run(
        source=source,
        started_at=NOW - timedelta(hours=ago_hours),
        finished_at=NOW - timedelta(hours=ago_hours),
        items_found=found,
        status=status,
    )
    session.add(row)
    session.flush()
    return row


def _offer(session, *, set_num=None, active=True, external_id="1"):
    if set_num:
        session.add(
            Set(
                set_num=set_num,
                name="Galaxy Explorer",
                name_normalized="galaxy explorer",
                rrp_eur=99.99,
                updated_at=NOW,
            )
        )
        session.flush()
    row = Offer(
        set_num=set_num,
        source="dealabs",
        external_id=external_id,
        title_raw="LEGO 10497",
        url="https://dealabs.test/1",
        first_seen_at=NOW,
        last_seen_at=NOW,
        is_active=active,
    )
    session.add(row)
    session.flush()
    return row


def _collect(session):
    return collect_health(session, now=NOW)


def test_an_empty_database_reports_nothing_without_crashing(session):
    report = _collect(session)
    assert report.sources == []
    assert report.active_offers == 0
    assert report.resolution_rate is None, "no offers means no rate, not zero"


def test_the_last_run_and_last_success_are_reported_per_source(session):
    _run(session, status="ok", ago_hours=5)
    _run(session, status="error", found=0, ago_hours=1)
    session.commit()

    (source,) = _collect(session).sources
    assert source.source == "dealabs"
    assert source.last_status == "error"
    assert source.last_run_at == NOW - timedelta(hours=1)
    assert source.last_ok_at == NOW - timedelta(hours=5)


def test_each_source_is_reported_separately(session):
    _run(session, source="dealabs")
    _run(session, source="somewhere-else")
    session.commit()

    assert [s.source for s in _collect(session).sources] == [
        "dealabs",
        "somewhere-else",
    ]


def test_a_running_row_does_not_count_as_the_last_run(session):
    """A run in flight has decided nothing yet."""
    _run(session, status="ok", ago_hours=2)
    _run(session, status="running", ago_hours=0)
    session.commit()

    (source,) = _collect(session).sources
    assert source.last_status == "ok"


def test_three_empty_runs_in_a_row_look_dead(session):
    """SPEC.md section 7: the only way to notice a broken parser."""
    for ago in (3, 2, 1):
        _run(session, found=0, ago_hours=ago)
    session.commit()

    (source,) = _collect(session).sources
    assert source.consecutive_empty == 3
    assert source.looks_dead is True


def test_two_empty_runs_are_not_yet_a_death(session):
    _run(session, found=30, ago_hours=3)
    _run(session, found=0, ago_hours=2)
    _run(session, found=0, ago_hours=1)
    session.commit()

    (source,) = _collect(session).sources
    assert source.consecutive_empty == 2
    assert source.looks_dead is False


def test_a_good_run_breaks_the_streak(session):
    """The streak counts backwards from now, not over all history."""
    for ago in (5, 4, 3):
        _run(session, found=0, ago_hours=ago)
    _run(session, found=30, ago_hours=1)
    session.commit()

    (source,) = _collect(session).sources
    assert source.consecutive_empty == 0
    assert source.looks_dead is False


def test_three_failures_in_a_row_look_dead(session):
    for ago in (3, 2, 1):
        _run(session, status="error", found=0, ago_hours=ago)
    session.commit()

    (source,) = _collect(session).sources
    assert source.consecutive_failed == 3
    assert source.looks_dead is True


def test_active_and_total_offers_are_counted_apart(session):
    _offer(session, external_id="a", active=True)
    _offer(session, external_id="b", active=False)
    session.commit()

    report = _collect(session)
    assert (report.active_offers, report.total_offers) == (1, 2)


def test_the_resolution_rate_covers_the_recent_sample(session):
    _offer(session, external_id="resolved", set_num="10497-1")
    _offer(session, external_id="unresolved")
    session.commit()

    report = _collect(session)
    assert (report.resolved_in_sample, report.sample_size) == (1, 2)
    assert report.resolution_rate == pytest.approx(0.5)


def test_only_alerts_inside_the_window_are_counted(session):
    offer = _offer(session, set_num="10497-1")
    for ago_days in (1, 6, 8):
        session.add(
            Alert(
                offer_id=offer.id,
                channel_id="c",
                price_eur=10.0,
                reason="discount_threshold",
                sent_at=NOW - timedelta(days=ago_days),
            )
        )
    session.commit()

    assert _collect(session).alerts_last_7_days == 2


def test_the_catalogue_reports_how_much_of_it_has_a_price(session):
    session.add_all(
        [
            Set(
                set_num="1-1",
                name="With",
                name_normalized="with",
                rrp_eur=10.0,
                updated_at=NOW,
            ),
            Set(
                set_num="2-1",
                name="Without",
                name_normalized="without",
                rrp_eur=None,
                updated_at=NOW,
            ),
        ]
    )
    session.commit()

    report = _collect(session)
    assert (report.catalogue_sets, report.catalogue_with_rrp) == (2, 1)
