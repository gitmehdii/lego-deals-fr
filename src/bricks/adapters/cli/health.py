import argparse
from collections.abc import Sequence

from sqlalchemy.engine import make_url

from bricks.config import Settings, get_settings
from bricks.log import configure_logging

_SECTIONS = (
    "Last successful run per source",
    "Active offers",
    "Resolution rate (last 100 offers)",
    "Alerts sent (last 7 days)",
)


def _render(settings: Settings) -> str:
    """Never print DATABASE_URL: a libSQL URL carries an auth token."""
    lines = [
        "bricks pipeline health",
        "======================",
        "",
        f"Database driver            {make_url(settings.database_url).drivername}",
        f"BRICKSET_API_KEY set       {settings.brickset_api_key is not None}",
        f"DISCORD_WEBHOOK_URL set    {settings.discord_webhook_url is not None}",
        "",
    ]
    for section in _SECTIONS:
        lines += [section, "  —", ""]
    lines.append("(counters land in lot 6)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.health",
        description="Report the health of the pipeline.",
    )
    parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    print(_render(settings))
    return 0
