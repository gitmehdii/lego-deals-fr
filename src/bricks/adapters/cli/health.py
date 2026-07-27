import argparse
from collections.abc import Sequence

from sqlalchemy.engine import make_url

from bricks.config import Settings, get_settings
from bricks.log import configure_logging

_SECTIONS = (
    "Dernier run réussi par source",
    "Offres actives",
    "Taux de résolution (100 dernières offres)",
    "Alertes envoyées (7 derniers jours)",
)


def _render(settings: Settings) -> str:
    """Never print DATABASE_URL: a libSQL URL carries an auth token."""
    lines = [
        "Santé du pipeline — bricks",
        "==========================",
        "",
        f"Base de données           {make_url(settings.database_url).drivername}",
        f"BRICKSET_API_KEY présent  {settings.brickset_api_key is not None}",
        f"DISCORD_WEBHOOK_URL prés. {settings.discord_webhook_url is not None}",
        "",
    ]
    for section in _SECTIONS:
        lines += [section, "  —", ""]
    lines.append("(compteurs renseignés au lot 6)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bricks.health",
        description="Affiche l'état de santé du pipeline.",
    )
    parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    print(_render(settings))
    return 0
