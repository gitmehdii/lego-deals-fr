import argparse
from collections.abc import Sequence

from bricks.config import get_settings
from bricks.db.session import create_db_engine, create_session_factory
from bricks.log import configure_logging, get_logger, redact_secrets
from bricks.services.ingest import IngestReport, ingest
from bricks.sources.http import HttpFetcher
from bricks.sources.registry import SOURCE_NAMES, build_source


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.ingest",
        description="Fetch offers from a source and store them.",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=SOURCE_NAMES,
        help="Name of the source to read.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger(__name__)

    session_factory = create_session_factory(create_db_engine(settings))
    with HttpFetcher() as fetcher, session_factory() as session:
        try:
            report = ingest(session, build_source(args.source, fetcher, settings))
        except Exception as exc:
            # The run row is already written by the service, with the reason.
            # Nothing is re-logged here beyond the exit path.
            log.error("ingest_aborted", source=args.source, error=redact_secrets(exc))
            return 1

    print(_render(report))
    return 0


def _render(report: IngestReport) -> str:
    return "\n".join(
        [
            f"ingest: {report.source}",
            "=" * (8 + len(report.source)),
            "",
            f"Run                        {report.run_id}",
            f"Offers found               {report.items_found}",
            f"Offers new                 {report.items_new}",
            f"Price points recorded      {report.price_points}",
        ]
    )
