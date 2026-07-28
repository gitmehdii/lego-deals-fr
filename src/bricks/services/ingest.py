"""Ingestion: pull offers from a source, resolve them, record what was seen.

The contract that matters here is that a run always leaves a trace. A crash
mid-way still writes its `runs` row with the reason, because a pipeline whose
failures are invisible is a pipeline nobody notices has died.

Detection and alerting are lot 5. A resolved offer is stored and nothing more
happens to it yet.
"""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bricks.core.resolve import Resolution, SetIndex, resolve
from bricks.db.models import Offer, PricePoint, Run
from bricks.log import get_logger, redact_secrets, run_context
from bricks.services.resolution import load_set_index
from bricks.sources.base import Source
from bricks.sources.models import RawOffer

_log = get_logger(__name__)


# Offers that have never been judged are caught up at the start of a run. The
# cap bounds the work: without it, the first run after a resolver change would
# sweep the whole table.
_CATCH_UP_LIMIT = 500


class IngestReport(BaseModel):
    run_id: int
    source: str
    items_found: int = 0
    items_new: int = 0
    items_resolved: int = 0
    price_points: int = 0
    # Offers judged for the first time, having been stored before a resolver
    # existed or before the catalogue knew their set.
    caught_up: int = 0


def ingest(
    session: Session, source: Source, *, min_resolution_score: float = 0.85
) -> IngestReport:
    """Fetch from `source`, resolve and persist. Records the run whatever happens.

    Re-raises after writing the error row: swallowing the failure here would
    hand the CLI a success it has no way to question.
    """
    run_id = _start_run(session, source.name)

    with run_context(run_id):
        try:
            report = _ingest_offers(session, source, run_id, min_resolution_score)
        except Exception as exc:
            # The session may be mid-failure, so drop whatever it holds before
            # writing the trace. The row matters more than the partial work.
            session.rollback()
            _finish_run(session, run_id, status="error", error=redact_secrets(exc))
            _log.error("ingest_failed", source=source.name, error=redact_secrets(exc))
            raise

        _finish_run(
            session,
            run_id,
            status="ok",
            items_found=report.items_found,
            items_new=report.items_new,
            items_resolved=report.items_resolved,
        )
        _log.info("ingest_finished", **report.model_dump())
        return report


def _start_run(session: Session, source_name: str) -> int:
    run = Run(source=source_name, started_at=datetime.now(UTC), status="running")
    session.add(run)
    # Committed immediately: a process killed halfway must still leave evidence
    # that a run was attempted.
    session.commit()
    return run.id


def _finish_run(
    session: Session,
    run_id: int,
    *,
    status: str,
    error: str | None = None,
    items_found: int = 0,
    items_new: int = 0,
    items_resolved: int = 0,
) -> None:
    """Written as a Core UPDATE by id, so it works even after a rollback."""
    session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(
            status=status,
            error=error,
            finished_at=datetime.now(UTC),
            items_found=items_found,
            items_new=items_new,
            items_resolved=items_resolved,
        )
    )
    session.commit()


def _ingest_offers(
    session: Session, source: Source, run_id: int, min_resolution_score: float
) -> IngestReport:
    raw_offers = source.fetch()
    report = IngestReport(run_id=run_id, source=source.name)
    index = load_set_index(session)
    report.caught_up = _catch_up_unjudged(session, index, min_resolution_score)

    seen: set[str] = set()
    now = datetime.now(UTC)
    for raw in raw_offers:
        if raw.external_id in seen:
            # A feed that lists the same deal twice must not break the run on
            # a unique constraint.
            _log.warning("duplicate_in_feed", external_id=raw.external_id)
            continue
        seen.add(raw.external_id)
        report.items_found += 1

        offer = session.scalars(
            select(Offer).where(
                Offer.source == source.name, Offer.external_id == raw.external_id
            )
        ).one_or_none()

        if offer is None:
            offer = _new_offer(source.name, raw, now)
            session.add(offer)
            session.flush()
            report.items_new += 1
        else:
            _refresh_offer(offer, raw, now)

        # Re-run every time rather than only for new offers: the catalogue
        # grows and the resolver improves, and an offer resolved badly last
        # week deserves the benefit of both.
        _apply_resolution(
            offer,
            resolve(raw.title, index, merchant=raw.merchant),
            min_resolution_score,
        )
        if offer.set_num is not None:
            report.items_resolved += 1

        if raw.price_eur is not None:
            # One row per observation, even when the price has not moved:
            # "we looked and it was still 79,99" is itself information.
            session.add(
                PricePoint(offer_id=offer.id, price_eur=raw.price_eur, observed_at=now)
            )
            report.price_points += 1

    session.commit()
    return report


def _catch_up_unjudged(
    session: Session, index: SetIndex, min_resolution_score: float
) -> int:
    """Judge offers stored before any resolver could look at them.

    An offer that has rotated out of the feed is never seen again, so without
    this its resolution columns stay NULL forever and drag down the rate the
    health command reports. Each offer is judged once: `resolution_method`
    stops being NULL whatever the verdict.
    """
    unjudged = session.scalars(
        select(Offer)
        .where(Offer.resolution_method.is_(None))
        .order_by(Offer.id.desc())
        .limit(_CATCH_UP_LIMIT)
    ).all()

    judged = 0
    for offer in unjudged:
        resolution = resolve(offer.title_raw, index, merchant=offer.merchant)
        if resolution.method is None:
            # Nothing to judge, and nothing to record. It will be retried on a
            # later run, when the catalogue may know more.
            continue
        _apply_resolution(offer, resolution, min_resolution_score)
        judged += 1

    if judged:
        session.commit()
        _log.info("offers_caught_up", judged=judged, considered=len(unjudged))
    return judged


def _apply_resolution(
    offer: Offer, resolution: Resolution, min_resolution_score: float
) -> None:
    """SPEC.md wants the score and method always stored, so resolution quality
    can be measured rather than guessed. schema.sql wants NULL when unresolved.
    Both hold: a verdict is recorded even when rejected, and NULL means the
    resolver found nothing to judge."""
    found_something = resolution.method is not None
    offer.resolution_score = resolution.score if found_something else None
    offer.resolution_method = resolution.method
    offer.set_num = (
        resolution.set_num if resolution.accepted(min_resolution_score) else None
    )


def _new_offer(source_name: str, raw: RawOffer, now: datetime) -> Offer:
    return Offer(
        # Filled in by the resolver just after the flush, once the row has an
        # id. An offer that stays NULL here simply never produces an alert.
        set_num=None,
        resolution_score=None,
        resolution_method=None,
        source=source_name,
        external_id=raw.external_id,
        merchant=raw.merchant,
        title_raw=raw.title,
        url=raw.url,
        current_price_eur=raw.price_eur,
        first_seen_at=raw.published_at or now,
        last_seen_at=now,
        is_active=True,
    )


def _refresh_offer(offer: Offer, raw: RawOffer, now: datetime) -> None:
    """title_raw is never overwritten: it is the evidence of what we resolved."""
    offer.last_seen_at = now
    offer.is_active = True
    if raw.price_eur is not None:
        # A source that stops publishing a price does not make the last one we
        # saw untrue, so an absent price leaves the field alone.
        offer.current_price_eur = raw.price_eur
    if raw.merchant:
        offer.merchant = raw.merchant
