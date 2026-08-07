from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from bricks.db.models import Alert, HealthAlert, Offer, Run, Set
from bricks.services.health import (
    CONSECUTIVE_BAD_RUNS_BEFORE_WARNING,
    MAX_HOURS_WITHOUT_A_RUN,
    collect_health,
    warn_about_dead_sources,
)

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


# --- warning about dead sources --------------------------------------------


class WarningRecorder:
    def __init__(self):
        self.sent = []

    def __call__(self, warning):
        self.sent.append(warning)


def _warn(session, send=None, now=NOW):
    return warn_about_dead_sources(session, send=send, now=now)


def _kill(session, *, status="ok", found=0, count=3):
    for ago in range(count, 0, -1):
        _run(session, status=status, found=found, ago_hours=ago)
    session.commit()


def test_a_healthy_source_produces_no_warning(session):
    _run(session, found=30)
    session.commit()
    assert _warn(session, WarningRecorder()) == []


def test_three_empty_runs_warn_once(session):
    _kill(session)
    recorder = WarningRecorder()

    warnings = _warn(session, recorder)

    assert len(warnings) == 1
    assert warnings[0].reason == "no_items"
    assert warnings[0].source == "dealabs"
    assert len(recorder.sent) == 1
    assert session.scalar(select(func.count()).select_from(HealthAlert)) == 1


def test_three_failures_warn_with_the_more_telling_reason(session):
    """An exception says more than a zero count, so it wins."""
    _kill(session, status="error")
    (warning,) = _warn(session, WarningRecorder())
    assert warning.reason == "failing"


def test_a_second_run_inside_24h_does_not_warn_again(session):
    """SPEC.md: not repeated more than once per 24h."""
    _kill(session)
    recorder = WarningRecorder()

    _warn(session, recorder)
    again = _warn(session, recorder, now=NOW + timedelta(hours=23))

    assert again == []
    assert len(recorder.sent) == 1


def test_after_24h_the_warning_repeats(session):
    _kill(session)
    recorder = WarningRecorder()

    _warn(session, recorder)
    _warn(session, recorder, now=NOW + timedelta(hours=25))

    assert len(recorder.sent) == 2


def test_the_dry_run_warns_nobody_and_records_nothing(session):
    _kill(session)

    warnings = _warn(session, send=None)

    assert len(warnings) == 1, "still computed, so --dry-run can show it"
    assert session.scalar(select(func.count()).select_from(HealthAlert)) == 0


def test_a_failed_send_records_no_warning(session):
    """The table counts warnings actually delivered, like alerts does."""
    _kill(session)

    def explode(warning):
        raise RuntimeError("discord is down")

    with pytest.raises(RuntimeError):
        _warn(session, explode)

    session.rollback()
    assert session.scalar(select(func.count()).select_from(HealthAlert)) == 0


def test_each_source_is_warned_about_separately(session):
    _kill(session)
    _run(session, source="other", found=30)
    session.commit()

    (warning,) = _warn(session, WarningRecorder())
    assert warning.source == "dealabs"


def test_the_warning_carries_the_streak_and_the_last_success(session):
    _run(session, found=30, ago_hours=10)
    _kill(session)

    (warning,) = _warn(session, WarningRecorder())
    assert warning.consecutive_runs == 3
    assert warning.last_ok_at is not None


# --- staleness: the failure nobody records ---------------------------------


def _ok_run(session, source="dealabs", at=None, items=5):
    session.add(
        Run(
            source=source,
            started_at=at or NOW,
            finished_at=at or NOW,
            status="ok",
            items_found=items,
        )
    )
    session.flush()


def test_a_source_that_simply_stopped_running_looks_dead(session):
    """The hole this closes: a run cancelled before it starts writes no row,
    so no streak can count it and every other rule stays silent."""
    _ok_run(session, at=NOW - timedelta(hours=MAX_HOURS_WITHOUT_A_RUN + 1))
    session.commit()

    (source,) = collect_health(session, now=NOW).sources

    assert source.consecutive_failed == 0, "nothing failed; nothing ran at all"
    assert source.consecutive_empty == 0
    assert source.looks_dead
    assert source.death_reason == "stale"


def test_a_normal_overnight_lull_is_not_death(session):
    """Measured in production: the longest real gap was 9.5 hours, and a
    warning that fires on a quiet night is one you learn to ignore."""
    _ok_run(session, at=NOW - timedelta(hours=9.5))
    session.commit()

    (source,) = collect_health(session, now=NOW).sources
    assert not source.looks_dead


def test_a_recorded_failure_outranks_mere_silence(session):
    """`failing` says more than `stale`: one was observed, the other inferred."""
    for index in range(CONSECUTIVE_BAD_RUNS_BEFORE_WARNING):
        session.add(
            Run(
                source="dealabs",
                started_at=NOW - timedelta(hours=20 + index),
                status="error",
                items_found=0,
            )
        )
    session.commit()

    (source,) = collect_health(session, now=NOW).sources
    assert source.death_reason == "failing"


def test_the_stale_warning_says_hours_not_runs(session):
    """It counts no runs on purpose — there were none to count."""
    _ok_run(session, at=NOW - timedelta(hours=30))
    session.commit()

    (warning,) = warn_about_dead_sources(session, send=None, now=NOW)

    assert warning.reason == "stale"
    assert warning.consecutive_runs == 0
    assert warning.hours_since_last_ok == pytest.approx(30.0, abs=0.1)
