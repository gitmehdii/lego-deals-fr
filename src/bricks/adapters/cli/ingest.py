import argparse
from collections.abc import Sequence

from bricks.config import get_settings
from bricks.log import configure_logging, get_logger


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.ingest",
        description="Fetch offers from a source and store them.",
    )
    parser.add_argument("--source", required=True, help="Name of the source to read.")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    get_logger(__name__).warning("source_not_implemented", source=args.source)
    return 0
