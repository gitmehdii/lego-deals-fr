from datetime import UTC, datetime, timedelta

import pytest

from bricks.core.detect import (
    MAX_ALERTS_PER_RUN,
    Candidate,
    Decision,
    discount_pct,
    evaluate,
    rank_for_sending,
)

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
MIN_DISCOUNT = 25.0


def _evaluate(candidate: Candidate, *, now=NOW):
    return evaluate(candidate, min_discount_pct=MIN_DISCOUNT, now=now)


# --- discount --------------------------------------------------------------


def test_discount_is_a_percentage_never_a_ratio():
    """Same unit as MIN_DISCOUNT_PCT and alerts.discount_pct, so no conversion."""
    assert discount_pct(69.99, 99.99) == pytest.approx(30.0, abs=0.01)


@pytest.mark.parametrize("rrp", [None, 0, -10.0])
def test_no_honest_reference_means_no_discount(rrp):
    assert discount_pct(69.99, rrp) is None


def test_a_price_above_the_rrp_gives_a_negative_discount():
    """The truth, and simply never above a threshold."""
    assert discount_pct(120.0, 100.0) == pytest.approx(-20.0)


# --- criterion A, discount threshold ---------------------------------------


def test_a_deep_enough_discount_alerts():
    decision = _evaluate(Candidate(offer_id=1, price_eur=69.99, rrp_eur=99.99))
    assert decision.reason == "discount_threshold"
    assert decision.discount_pct == pytest.approx(30.0, abs=0.01)


def test_a_shallow_discount_does_not_alert():
    """SPEC.md's worked example: 20 % against a 25 % threshold, no alert."""
    decision = _evaluate(Candidate(offer_id=1, price_eur=79.99, rrp_eur=99.99))
    assert not decision.should_alert
    assert decision.discount_pct == pytest.approx(20.0, abs=0.01)


def test_exactly_the_threshold_alerts():
    decision = _evaluate(Candidate(offer_id=1, price_eur=75.0, rrp_eur=100.0))
    assert decision.reason == "discount_threshold"


def test_without_a_rrp_the_threshold_criterion_cannot_fire():
    decision = _evaluate(Candidate(offer_id=1, price_eur=10.0, rrp_eur=None))
    assert not decision.should_alert
    assert decision.discount_pct is None


# --- criterion B, all-time low ---------------------------------------------


def test_a_new_low_alerts_even_without_a_rrp():
    """Criterion B needs no reference price at all."""
    decision = _evaluate(
        Candidate(
            offer_id=1, price_eur=60.0, rrp_eur=None, previous_prices=(80.0, 75.0, 70.0)
        )
    )
    assert decision.reason == "all_time_low"


def test_a_new_low_needs_three_observations_behind_it():
    """ "Never seen this cheap" is meaningless with two prices behind it."""
    decision = _evaluate(
        Candidate(
            offer_id=1, price_eur=60.0, rrp_eur=None, previous_prices=(80.0, 75.0)
        )
    )
    assert not decision.should_alert


def test_equalling_the_record_is_not_beating_it():
    decision = _evaluate(
        Candidate(
            offer_id=1, price_eur=70.0, rrp_eur=None, previous_prices=(80.0, 75.0, 70.0)
        )
    )
    assert not decision.should_alert


def test_the_all_time_low_is_reported_when_both_criteria_hold():
    """The rarer fact, and the more interesting one to read."""
    decision = _evaluate(
        Candidate(
            offer_id=1,
            price_eur=50.0,
            rrp_eur=100.0,
            previous_prices=(80.0, 75.0, 70.0),
        )
    )
    assert decision.reason == "all_time_low"
    assert decision.discount_pct == pytest.approx(50.0)


def test_spec_worked_example_no_alert_then_alert():
    """SPEC.md section 1, told as a story: 79,99 then 69,99 the next day."""
    history = (74.90, 80.0, 99.99)
    quiet = _evaluate(
        Candidate(offer_id=1, price_eur=79.99, rrp_eur=99.99, previous_prices=history)
    )
    assert not quiet.should_alert, "20 % off and not the lowest ever"

    loud = _evaluate(
        Candidate(offer_id=1, price_eur=69.99, rrp_eur=99.99, previous_prices=history)
    )
    assert loud.reason == "all_time_low"


# --- anti-spam -------------------------------------------------------------


def test_no_second_alert_for_the_same_offer_within_24h():
    decision = _evaluate(
        Candidate(
            offer_id=1,
            price_eur=50.0,
            rrp_eur=100.0,
            last_alert_at=NOW - timedelta(hours=23),
            last_alert_price_eur=60.0,
        )
    )
    assert not decision.should_alert
    assert decision.suppressed_by == "alerted_within_24h"


def test_after_24h_a_real_drop_alerts_again():
    decision = _evaluate(
        Candidate(
            offer_id=1,
            price_eur=50.0,
            rrp_eur=100.0,
            last_alert_at=NOW - timedelta(hours=25),
            last_alert_price_eur=60.0,
        )
    )
    assert decision.reason == "discount_threshold"


def test_a_price_that_barely_moved_does_not_alert_again():
    """Under 5 % since the last alert is not news."""
    decision = _evaluate(
        Candidate(
            offer_id=1,
            price_eur=58.0,
            rrp_eur=100.0,
            last_alert_at=NOW - timedelta(days=3),
            last_alert_price_eur=60.0,
        )
    )
    assert not decision.should_alert
    assert decision.suppressed_by == "price_did_not_drop_enough"


def test_a_price_that_went_up_does_not_alert_again():
    decision = _evaluate(
        Candidate(
            offer_id=1,
            price_eur=70.0,
            rrp_eur=100.0,
            last_alert_at=NOW - timedelta(days=3),
            last_alert_price_eur=60.0,
        )
    )
    assert decision.suppressed_by == "price_did_not_drop_enough"


def test_suppression_is_recorded_even_though_nothing_is_sent():
    """Why we stayed quiet is worth logging as much as why we spoke."""
    decision = _evaluate(
        Candidate(
            offer_id=1,
            price_eur=50.0,
            rrp_eur=100.0,
            last_alert_at=NOW - timedelta(hours=1),
            last_alert_price_eur=60.0,
        )
    )
    assert decision.suppressed_by is not None
    assert decision.discount_pct == pytest.approx(50.0)


def test_anti_spam_never_resurrects_an_offer_that_did_not_qualify():
    """Suppression only ever removes alerts, it cannot add one."""
    decision = _evaluate(
        Candidate(offer_id=1, price_eur=95.0, rrp_eur=100.0, last_alert_at=None)
    )
    assert not decision.should_alert
    assert decision.suppressed_by is None


# --- ranking ---------------------------------------------------------------


def test_the_best_discounts_are_sent_first():
    """A capped run must keep the deals worth having."""
    pairs = [
        (Candidate(offer_id=1, price_eur=1, rrp_eur=None), Decision(discount_pct=30.0)),
        (Candidate(offer_id=2, price_eur=1, rrp_eur=None), Decision(discount_pct=60.0)),
        (Candidate(offer_id=3, price_eur=1, rrp_eur=None), Decision(discount_pct=None)),
        (Candidate(offer_id=4, price_eur=1, rrp_eur=None), Decision(discount_pct=45.0)),
    ]
    assert [c.offer_id for c, _ in rank_for_sending(pairs)] == [2, 4, 1, 3]


def test_a_record_low_without_a_rrp_is_not_what_the_cap_discards_first():
    """Most of the catalogue has no RRP, so its record lows carry no discount.

    Ranked on the discount alone they sorted as zero, behind every threshold
    alert, and a set falling to its cheapest price ever was the first thing a
    capped run threw away — the opposite of what _matching_criterion prefers.
    """
    pairs = [
        (
            Candidate(offer_id=1, price_eur=1, rrp_eur=None),
            Decision(reason="discount_threshold", discount_pct=60.0),
        ),
        (
            Candidate(offer_id=2, price_eur=1, rrp_eur=None),
            Decision(reason="all_time_low", discount_pct=None),
        ),
        (
            Candidate(offer_id=3, price_eur=1, rrp_eur=None),
            Decision(reason="all_time_low", discount_pct=70.0),
        ),
    ]
    ranked = [c.offer_id for c, _ in rank_for_sending(pairs)]

    assert ranked == [3, 2, 1], "record lows lead, then the deepest discount"


def test_the_run_cap_is_ten():
    """SPEC.md: hitting it is more often a bug than a black Friday."""
    assert MAX_ALERTS_PER_RUN == 10
