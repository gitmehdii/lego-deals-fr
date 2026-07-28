"""Catalogue synchronisation: the internal API for filling the `sets` table.

Two providers, two phases, two different refresh concerns:

  1. Rebrickable owns a set's identity — number, name, theme, year, pieces.
  2. Brickset owns its recommended retail price.

They are kept apart on purpose. An identity refresh must never be able to
wipe a price, and a price refresh must never be able to rename a set.

Knows nothing about Discord, imports nothing from `adapters/`.
"""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from bricks.db.models import Set
from bricks.log import get_logger
from bricks.sources.brickset import BricksetCatalogue
from bricks.sources.rebrickable import RebrickableCatalogue

# The columns Rebrickable owns. `rrp_eur` is pointedly not among them.
_IDENTITY_COLUMNS = ("name", "name_normalized", "theme", "year", "pieces", "image_url")

_log = get_logger(__name__)


class CatalogSyncReport(BaseModel):
    """What a sync did. `created == updated == 0` is what idempotence looks like."""

    sets_fetched: int = 0
    sets_created: int = 0
    sets_updated: int = 0

    rrp_skipped: bool = False
    rrp_years: int = 0
    rrp_fetched: int = 0
    rrp_updated: int = 0
    # Brickset knows the set but publishes no euro price for it.
    rrp_unknown: int = 0
    # Brickset knows a set our catalogue does not.
    rrp_unmatched: int = 0


def sync_catalogue(
    session: Session,
    *,
    rebrickable: RebrickableCatalogue,
    brickset: BricksetCatalogue | None = None,
    since_year: int | None = None,
) -> CatalogSyncReport:
    """Refresh `sets` from both providers. Safe to run twice.

    Phase 1 is committed before phase 2 starts, so an API that goes down
    mid-sync still leaves the identity import durable and the next run picks
    up from there.
    """
    report = CatalogSyncReport()
    _sync_identities(session, rebrickable, report)

    if brickset is None:
        report.rrp_skipped = True
        _log.info("catalog_rrp_skipped")
        return report

    _sync_retail_prices(session, brickset, since_year, report)
    return report


def _sync_identities(
    session: Session, rebrickable: RebrickableCatalogue, report: CatalogSyncReport
) -> None:
    incoming = rebrickable.fetch()
    report.sets_fetched = len(incoming)

    existing = {
        row.set_num: row
        for row in session.execute(
            select(Set.set_num, *(getattr(Set, name) for name in _IDENTITY_COLUMNS))
        ).all()
    }

    now = datetime.now(UTC)
    to_insert: list[dict[str, object]] = []
    to_update: list[dict[str, object]] = []
    for item in incoming:
        # model_dump() includes the computed name_normalized, so it can never
        # be forgotten here nor drift away from name.
        payload = item.model_dump() | {"updated_at": now}
        row = existing.get(item.set_num)
        if row is None:
            to_insert.append(payload)
        elif any(payload[name] != getattr(row, name) for name in _IDENTITY_COLUMNS):
            to_update.append(payload)

    if to_insert:
        session.execute(insert(Set), to_insert)
    if to_update:
        # Only the identity columns are in the payload, so rrp_eur survives.
        session.execute(update(Set), to_update)
    session.commit()

    report.sets_created = len(to_insert)
    report.sets_updated = len(to_update)
    _log.info(
        "catalog_identities_synced",
        fetched=report.sets_fetched,
        created=report.sets_created,
        updated=report.sets_updated,
    )


def _sync_retail_prices(
    session: Session,
    brickset: BricksetCatalogue,
    since_year: int | None,
    report: CatalogSyncReport,
) -> None:
    known = dict(session.execute(select(Set.set_num, Set.rrp_eur)).all())
    years = sorted(
        year
        for year in session.scalars(
            select(Set.year).where(Set.year.is_not(None)).distinct()
        )
        if since_year is None or year >= since_year
    )
    report.rrp_years = len(years)

    for year in years:
        prices = brickset.fetch_retail_prices(year)
        report.rrp_fetched += len(prices)

        now = datetime.now(UTC)
        changed: list[dict[str, object]] = []
        for price in prices:
            if price.set_num not in known:
                # Brickset lists references Rebrickable does not. Not an error.
                report.rrp_unmatched += 1
                continue
            if price.rrp_eur is None:
                # A price we already hold is better than none, so a set that
                # has since lost its euro price keeps the last one we saw.
                report.rrp_unknown += 1
                continue
            if known[price.set_num] == price.rrp_eur:
                continue
            changed.append(
                {"set_num": price.set_num, "rrp_eur": price.rrp_eur, "updated_at": now}
            )
            known[price.set_num] = price.rrp_eur

        if changed:
            session.execute(update(Set), changed)
        # Committed year by year: a rate limit halfway through keeps whatever
        # came before it.
        session.commit()
        report.rrp_updated += len(changed)

    _log.info(
        "catalog_retail_prices_synced",
        years=report.rrp_years,
        fetched=report.rrp_fetched,
        updated=report.rrp_updated,
        unknown=report.rrp_unknown,
        unmatched=report.rrp_unmatched,
    )
