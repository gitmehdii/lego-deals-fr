"""Deciding whether a price is worth interrupting someone for. Pure, no I/O.

Two independent criteria and three anti-spam rules, all from SPEC.md section 5.
Every number here is a percentage between 0 and 100, never a ratio: the config
(`MIN_DISCOUNT_PCT`) and the database (`alerts.discount_pct`) use that same
unit, so nothing in the codebase ever converts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

AlertReason = Literal["discount_threshold", "all_time_low"]

# "Never seen this cheap" is meaningless with two observations behind it.
MIN_OBSERVATIONS_FOR_ALL_TIME_LOW = 3

# Anti-spam, SPEC.md section 5.
MIN_HOURS_BETWEEN_ALERTS = 24
MIN_DROP_PCT_SINCE_LAST_ALERT = 5.0
MAX_ALERTS_PER_RUN = 10


def discount_pct(price_eur: float, rrp_eur: float | None) -> float | None:
    """(rrp - price) / rrp * 100, or None when no honest reference exists.

    Computed against the recommended retail price, never against a merchant's
    struck-through price, which is marketing rather than a fact.

    A price above the RRP yields a negative number, which is the truth and is
    simply never above any threshold.
    """
    if rrp_eur is None or rrp_eur <= 0:
        return None
    return (rrp_eur - price_eur) / rrp_eur * 100


@dataclass(frozen=True)
class Candidate:
    """Everything detection needs about one offer, gathered by `services/`."""

    offer_id: int
    price_eur: float
    rrp_eur: float | None

    # Every price ever observed for this *set*, all offers and merchants
    # together, excluding the observation being judged.
    previous_prices: tuple[float, ...] = ()

    last_alert_at: datetime | None = None
    last_alert_price_eur: float | None = None


@dataclass(frozen=True)
class Decision:
    """Why an alert fires, or why it does not. Both are worth logging."""

    reason: AlertReason | None = None
    discount_pct: float | None = None
    suppressed_by: str | None = None

    @property
    def should_alert(self) -> bool:
        return self.reason is not None


def evaluate(
    candidate: Candidate, *, min_discount_pct: float, now: datetime
) -> Decision:
    """Decide on one offer. Anti-spam wins over both criteria."""
    discount = discount_pct(candidate.price_eur, candidate.rrp_eur)
    reason = _matching_criterion(candidate, discount, min_discount_pct)
    if reason is None:
        return Decision(discount_pct=discount)

    if suppressed := _suppression(candidate, now):
        return Decision(discount_pct=discount, suppressed_by=suppressed)

    return Decision(reason=reason, discount_pct=discount)


def _matching_criterion(
    candidate: Candidate, discount: float | None, min_discount_pct: float
) -> AlertReason | None:
    """Criterion A or criterion B, independently, either one is enough.

    The all-time low is reported in preference to the threshold when both
    hold: it is the rarer fact and the more interesting one to read.
    """
    if _is_all_time_low(candidate):
        return "all_time_low"
    if discount is not None and discount >= min_discount_pct:
        return "discount_threshold"
    return None


def _is_all_time_low(candidate: Candidate) -> bool:
    """Strictly cheaper than everything ever seen for this set.

    Needs a real history behind it. Equalling the record is not beating it.
    """
    if len(candidate.previous_prices) < MIN_OBSERVATIONS_FOR_ALL_TIME_LOW:
        return False
    return candidate.price_eur < min(candidate.previous_prices)


def _suppression(candidate: Candidate, now: datetime) -> str | None:
    """The first two anti-spam rules. The third is a per-run cap, applied by
    `services/` because it needs to see every candidate at once."""
    if candidate.last_alert_at is None:
        return None

    if now - candidate.last_alert_at < timedelta(hours=MIN_HOURS_BETWEEN_ALERTS):
        return "alerted_within_24h"

    previous = candidate.last_alert_price_eur
    if previous is not None and previous > 0:
        drop = (previous - candidate.price_eur) / previous * 100
        if drop < MIN_DROP_PCT_SINCE_LAST_ALERT:
            return "price_did_not_drop_enough"

    return None


def rank_for_sending(
    decisions: list[tuple[Candidate, Decision]],
) -> list[tuple[Candidate, Decision]]:
    """Best deals first, so a capped run keeps the ones worth having.

    An all-time low leads whatever its discount, for the same reason
    `_matching_criterion` prefers it: it is the rarer fact. Ranking on the
    discount alone quietly did the opposite — a set with no RRP has no
    discount to be ranked by, so its record low sorted as zero and was the
    first thing a capped run threw away. Most of the catalogue has no RRP,
    and the cap is reached often enough for that to matter.
    """
    return sorted(
        decisions,
        key=lambda pair: (
            pair[1].reason == "all_time_low",
            pair[1].discount_pct if pair[1].discount_pct is not None else 0,
        ),
        reverse=True,
    )
