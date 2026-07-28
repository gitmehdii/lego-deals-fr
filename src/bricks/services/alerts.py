"""Detection and alerting: gather the facts, decide, record what was sent.

Contains no Discord vocabulary at all. It produces `AlertPayload`, which is
what a *reader* needs to know about a deal; turning that into an embed, a
console block or anything else is `adapters/`' problem. That separation is
what will let an MCP server or an API reuse this without a refactor.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from bricks.core.detect import (
    MAX_ALERTS_PER_RUN,
    AlertReason,
    Candidate,
    Decision,
    evaluate,
    rank_for_sending,
)
from bricks.db.models import Alert, Offer, PricePoint, Set
from bricks.log import get_logger

_log = get_logger(__name__)


class AlertPayload(BaseModel):
    """Everything a human needs to judge the deal. No presentation decisions."""

    offer_id: int
    set_num: str
    set_name: str
    url: str
    price_eur: float
    rrp_eur: float | None = None
    discount_pct: float | None = None
    reason: AlertReason
    merchant: str | None = None
    pieces: int | None = None
    theme: str | None = None
    year: int | None = None
    image_url: str | None = None

    # Only meaningful when `reason` is all_time_low: the record being beaten.
    previous_low_eur: float | None = None
    previous_low_at: datetime | None = None


class AlertsReport(BaseModel):
    considered: int = 0
    sent: int = 0
    suppressed: int = 0
    capped: bool = False


def detect_and_alert(
    session: Session,
    *,
    min_discount_pct: float,
    channel_id: str,
    send: Callable[[AlertPayload], None] | None = None,
    only_offer_ids: list[int] | None = None,
    now: datetime | None = None,
) -> tuple[AlertsReport, list[AlertPayload]]:
    """Evaluate resolved, priced, active offers and alert on the winners.

    `only_offer_ids` restricts the evaluation to what the current run actually
    observed. Alerting on a price nobody confirmed today would send a reader
    to a dead page.

    `send` is injected. Passing None is the dry run: everything is computed
    and returned, nothing is sent and no row is written to `alerts`.
    """
    now = now or datetime.now(UTC)
    report = AlertsReport()

    qualifying: list[tuple[Candidate, Decision, Offer, Set]] = []
    history_by_offer: dict[int, list[tuple[float, datetime]]] = {}
    for offer, lego_set in _alertable_offers(session, only_offer_ids):
        history = _price_history(session, offer)
        history_by_offer[offer.id] = history
        candidate = _build_candidate(session, offer, lego_set, history)
        decision = evaluate(candidate, min_discount_pct=min_discount_pct, now=now)
        report.considered += 1

        if decision.suppressed_by:
            report.suppressed += 1
            _log.info(
                "alert_suppressed",
                offer_id=offer.id,
                reason=decision.suppressed_by,
                discount_pct=decision.discount_pct,
            )
        if decision.should_alert:
            qualifying.append((candidate, decision, offer, lego_set))

    ranked = rank_for_sending([(c, d) for c, d, _, _ in qualifying])
    by_offer = {offer.id: (offer, lego_set) for _, _, offer, lego_set in qualifying}

    if len(ranked) > MAX_ALERTS_PER_RUN:
        report.capped = True
        # Far more often a bug than a black Friday, so it is said loudly.
        _log.warning(
            "alert_cap_reached", qualifying=len(ranked), cap=MAX_ALERTS_PER_RUN
        )

    payloads: list[AlertPayload] = []
    for candidate, decision in ranked[:MAX_ALERTS_PER_RUN]:
        offer, lego_set = by_offer[candidate.offer_id]
        payload = _build_payload(
            candidate, decision, offer, lego_set, history_by_offer[candidate.offer_id]
        )
        payloads.append(payload)

        if send is None:
            continue
        send(payload)
        # Written only after a successful send: the table means "messages we
        # actually delivered", and the anti-spam rules read it as such.
        session.add(
            Alert(
                offer_id=offer.id,
                guild_id=None,
                channel_id=channel_id,
                price_eur=payload.price_eur,
                discount_pct=payload.discount_pct,
                reason=payload.reason,
                sent_at=now,
            )
        )
        session.commit()
        report.sent += 1

    return report, payloads


def _alertable_offers(
    session: Session, only_offer_ids: list[int] | None
) -> list[tuple[Offer, Set]]:
    """Resolved, priced and still active. Everything else cannot alert."""
    query = (
        select(Offer, Set)
        .join(Set, Set.set_num == Offer.set_num)
        .where(
            Offer.set_num.is_not(None),
            Offer.current_price_eur.is_not(None),
            Offer.is_active.is_(True),
        )
    )
    if only_offer_ids is not None:
        query = query.where(Offer.id.in_(only_offer_ids))
    return list(session.execute(query).all())


def _build_candidate(
    session: Session,
    offer: Offer,
    lego_set: Set,
    history: list[tuple[float, datetime]],
) -> Candidate:
    last_alert = session.scalars(
        select(Alert).where(Alert.offer_id == offer.id).order_by(Alert.sent_at.desc())
    ).first()

    return Candidate(
        offer_id=offer.id,
        price_eur=offer.current_price_eur,
        rrp_eur=lego_set.rrp_eur,
        previous_prices=tuple(price for price, _ in history),
        last_alert_at=last_alert.sent_at if last_alert else None,
        last_alert_price_eur=last_alert.price_eur if last_alert else None,
    )


def _price_history(session: Session, offer: Offer) -> list[tuple[float, datetime]]:
    """Every price seen for this *set*, all offers and merchants together.

    The newest observation is dropped: an all-time low is judged against
    history, not against itself. Queried once per offer and reused for both
    the decision and the "previous record" line of the message.
    """
    rows = session.execute(
        select(PricePoint.price_eur, PricePoint.observed_at)
        .join(Offer, Offer.id == PricePoint.offer_id)
        .where(Offer.set_num == offer.set_num)
        .order_by(PricePoint.observed_at.desc())
    ).all()
    return [(price, observed_at) for price, observed_at in rows[1:]]


def _build_payload(
    candidate: Candidate,
    decision: Decision,
    offer: Offer,
    lego_set: Set,
    history: list[tuple[float, datetime]],
) -> AlertPayload:
    # Only carried for an all-time low, which is the only message that shows
    # the record being beaten.
    previous_low = (
        min(history, key=lambda row: row[0])
        if history and decision.reason == "all_time_low"
        else None
    )
    return AlertPayload(
        previous_low_eur=previous_low[0] if previous_low else None,
        previous_low_at=previous_low[1] if previous_low else None,
        offer_id=offer.id,
        set_num=lego_set.set_num,
        set_name=lego_set.name,
        url=offer.url,
        price_eur=candidate.price_eur,
        rrp_eur=candidate.rrp_eur,
        discount_pct=decision.discount_pct,
        reason=decision.reason,
        merchant=offer.merchant,
        pieces=lego_set.pieces,
        theme=lego_set.theme,
        year=lego_set.year,
        image_url=lego_set.image_url,
    )
