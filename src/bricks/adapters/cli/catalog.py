import argparse
from collections.abc import Sequence

from bricks.config import get_settings
from bricks.log import configure_logging, get_logger


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.catalog",
        description="Gère le catalogue des sets LEGO.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("sync", help="Met à jour la table sets.")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    get_logger(__name__).warning("catalog_not_implemented", command=args.command)
    return 0
