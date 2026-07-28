"""Ingestion: pull offers from a source and record what was seen.

The contract that matters here is that a run always leaves a trace. A crash
mid-way still writes its `runs` row with the reason, because a pipeline whose
failures are invisible is a pipeline nobody notices has died.

No resolution and no alerting yet; those are lots 4 and 5. Offers land with
`set_num` NULL on purpose.
"""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bricks.db.models import Offer, PricePoint, Run
from bricks.log import get_logger, redact_secrets, run_context
from bricks.sources.base import Source
from bricks.sources.models import RawOffer

_log = get_logger(__name__)


class IngestReport(BaseModel):
    run_id: int
    source: str
    items_found: int = 0
    items_new: int = 0
    price_points: int = 0


def ingest(session: Session, source: Source) -> IngestReport:
    """Fetch from `source` and persist. Records the run whatever happens.

    Re-raises after writing the error row: swallowing the failure here would
    hand the CLI a success it has no way to question.
    """
    run_id = _start_run(session, source.name)

    with run_context(run_id):
        try:
            report = _ingest_offers(session, source, run_id)
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
            # Resolution arrives in lot 4; the counter stays honest until then.
            items_resolved=0,
        )
    )
    session.commit()


def _ingest_offers(session: Session, source: Source, run_id: int) -> IngestReport:
    raw_offers = source.fetch()
    report = IngestReport(run_id=run_id, source=source.name)

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

        if raw.price_eur is not None:
            # One row per observation, even when the price has not moved:
            # "we looked and it was still 79,99" is itself information.
            session.add(
                PricePoint(offer_id=offer.id, price_eur=raw.price_eur, observed_at=now)
            )
            report.price_points += 1

    session.commit()
    return report


def _new_offer(source_name: str, raw: RawOffer, now: datetime) -> Offer:
    return Offer(
        # Resolution is lot 4. Until then every offer is unresolved, and an
        # unresolved offer never produces an alert.
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
