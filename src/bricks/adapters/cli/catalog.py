import argparse
from collections.abc import Sequence

from bricks.adapters.cli.common import configure, load_settings
from bricks.config import Settings
from bricks.db.session import create_db_engine, create_session_factory
from bricks.log import get_logger, redact_secrets
from bricks.services.catalog import CatalogSyncReport, sync_catalogue
from bricks.sources.brickset import BricksetCatalogue
from bricks.sources.http import HttpFetcher, SourceUnavailableError
from bricks.sources.rebrickable import RebrickableCatalogue


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.catalog",
        description="Manage the LEGO set catalogue.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="Refresh the sets table.")
    sync.add_argument(
        "--since-year",
        type=int,
        metavar="YEAR",
        help="Only fetch retail prices for sets released from YEAR onwards.",
    )
    sync.add_argument(
        "--skip-rrp",
        action="store_true",
        help="Import set identities only, without calling Brickset.",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    if settings is None:
        return 2
    configure(settings)
    log = get_logger(__name__)

    session_factory = create_session_factory(create_db_engine(settings))
    with HttpFetcher() as fetcher, session_factory() as session:
        try:
            report = sync_catalogue(
                session,
                rebrickable=RebrickableCatalogue(
                    fetcher,
                    sets_url=settings.rebrickable_sets_url,
                    themes_url=settings.rebrickable_themes_url,
                ),
                brickset=_brickset(fetcher, settings, skip=args.skip_rrp),
                since_year=args.since_year,
            )
        except SourceUnavailableError as exc:
            # Whatever was committed before the failure stays committed.
            log.error("catalog_sync_failed", error=redact_secrets(exc))
            return 1

    print(_render(report))
    return 0


def _brickset(
    fetcher: HttpFetcher, settings: Settings, *, skip: bool
) -> BricksetCatalogue | None:
    """None means the retail price phase does not run.

    A missing key is not an error: importing set identities on its own is
    still worth doing, and it is the whole command without a Brickset account.
    """
    if skip:
        return None
    if settings.brickset_api_key is None:
        get_logger(__name__).warning("brickset_api_key_missing")
        return None
    return BricksetCatalogue(
        fetcher,
        api_url=settings.brickset_api_url,
        api_key=settings.brickset_api_key,
    )


def _render(report: CatalogSyncReport) -> str:
    lines = [
        "catalogue sync",
        "==============",
        "",
        f"Sets fetched               {report.sets_fetched}",
        f"Sets created               {report.sets_created}",
        f"Sets updated               {report.sets_updated}",
        "",
    ]
    if report.rrp_skipped:
        lines.append("Retail prices              skipped")
        return "\n".join(lines)

    lines += [
        "Retail prices",
        f"  Years queried            {report.rrp_years}",
        f"  Prices fetched           {report.rrp_fetched}",
        f"  Prices updated           {report.rrp_updated}",
        f"  No euro price            {report.rrp_unknown}",
        f"  Not in the catalogue     {report.rrp_unmatched}",
    ]
    return "\n".join(lines)
